"""Nightly self-learning pass over recent CS conversations.

For each conversation that's gone quiet (no update in the last hour) and
hasn't been learned from yet, ask Claude to classify every customer/bot turn
(intent + outcome), then distill a handful of durable "memory" notes — things
the agent should remember next time (a clarification customers keep needing,
a wording fix, a recurring pain point). Memories get injected into the text
handler's system prompt; intents and outcomes feed cs_intent_patterns and
cs_reply_outcomes for visibility into what the bot is actually being asked.

Cheap by design: one Claude call per conversation (batches all its turns),
one summarizing call per run. Uses the CS agent's own Haiku model/key — same
workload, no extra billing surface.

Run from orchestrator/:
  python -m cs_agent.nightly_learner
"""
import json
import os
import sys

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from db import get_connection  # noqa: E402

from . import config, memory  # noqa: E402
from .claude_client import client  # noqa: E402

LOOKBACK_HOURS = int(os.getenv('CS_LEARNER_LOOKBACK_HOURS', '48'))
QUIET_MINUTES = int(os.getenv('CS_LEARNER_QUIET_MINUTES', '60'))
MAX_CONVERSATIONS = int(os.getenv('CS_LEARNER_MAX_CONVERSATIONS', '200'))
MAX_MEMORIES = int(os.getenv('CS_LEARNER_MAX_MEMORIES', '8'))

_VALID_OUTCOMES = {'resolved', 'escalated', 'repeated', 'unclear'}

_CLASSIFY_PROMPT = (
    "You are analyzing a past customer-support conversation for a Bangladeshi "
    "Gen Z fashion brand (Winterfell). The conversation is a list of turns "
    "(customer message + bot reply). For EACH turn, classify it.\n\n"
    "Return ONLY a JSON array, one object per turn, in order:\n"
    '[{"intent": "stock_check|price|size_guidance|order_status|delivery_time|'
    'return_policy|complaint|other", "outcome": "resolved|escalated|repeated|unclear", '
    '"notable_issue": "short note or empty string"}]\n\n'
    "outcome meanings: resolved = customer got what they needed; escalated = handed "
    "to a human or customer asked for one; repeated = customer asked the same thing "
    "again, suggesting the first answer didn't land; unclear = can't tell.\n"
    "notable_issue: only fill this in if the bot's reply was wrong, confusing, or "
    "missed something — otherwise leave it \"\".\n"
    "Output JSON only, no prose."
)

_DISTILL_PROMPT = (
    "You are improving a customer-support bot for Winterfell, a Bangladeshi Gen Z "
    "fashion brand. Below are classified turns from recent conversations, with any "
    "notable issues flagged.\n\n"
    "Distill at most {max_memories} short, concrete memory notes the bot should "
    "remember going forward — things like a clarification customers keep needing, "
    "a wording that confused people, or a policy detail that's commonly misunderstood. "
    "Skip anything that's a one-off; only include patterns that showed up more than "
    "once or reflect a real gap.\n\n"
    "Return ONLY a JSON array:\n"
    '[{"memory_text": "...", "category": "policy_clarification|faq|pitfall|other", '
    '"importance": 1-5}]\n'
    "If nothing is worth remembering, return [].\n"
    "Output JSON only, no prose."
)


def _parse_json_array(raw):
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.strip('`')
        raw = raw[raw.find('['):] if '[' in raw else raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _fetch_unlearned_conversations(conn):
    cutoff_active = f"NOW() - INTERVAL '{QUIET_MINUTES} minutes'"
    cutoff_window = f"NOW() - INTERVAL '{LOOKBACK_HOURS} hours'"
    rows = conn.execute(text(f"""
        SELECT id, messages FROM cs_conversations
        WHERE learned_at IS NULL
          AND updated_at < {cutoff_active}
          AND updated_at > {cutoff_window}
          AND messages IS NOT NULL AND messages <> '[]'
        ORDER BY updated_at ASC
        LIMIT :limit
    """), {'limit': MAX_CONVERSATIONS}).fetchall()
    return [(r._mapping['id'], memory._coerce(r._mapping['messages'], [])) for r in rows]


def _pair_turns(messages):
    """Messages are a flat role/content list; pair consecutive user→assistant turns."""
    pairs = []
    pending_user = None
    for m in messages:
        if m.get('role') == 'user':
            pending_user = m.get('content')
        elif m.get('role') == 'assistant' and pending_user is not None:
            pairs.append((pending_user, m.get('content')))
            pending_user = None
    return pairs


def _classify_conversation(pairs):
    if not pairs:
        return []
    turns_text = '\n\n'.join(
        f"Turn {i+1}:\nCustomer: {c}\nBot: {b}" for i, (c, b) in enumerate(pairs)
    )
    try:
        resp = client.messages.create(
            model=config.TEXT_MODEL, max_tokens=1000,
            system=_CLASSIFY_PROMPT,
            messages=[{'role': 'user', 'content': turns_text}],
        )
        raw = ''.join(b.text for b in resp.content if b.type == 'text')
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Classification call failed: {e}', flush=True)
        return []
    classified = _parse_json_array(raw)
    return classified[:len(pairs)]


def _record_outcomes(conn, conversation_id, pairs, classified):
    for (customer_msg, bot_reply), c in zip(pairs, classified):
        intent = (c.get('intent') or 'other')[:50]
        outcome = c.get('outcome') if c.get('outcome') in _VALID_OUTCOMES else 'unclear'
        notes = (c.get('notable_issue') or '').strip()[:1000] or None

        conn.execute(text("""
            INSERT INTO cs_reply_outcomes
              (conversation_id, customer_message, bot_reply, intent, outcome, notes)
            VALUES (:cid, :cm, :br, :intent, :outcome, :notes)
        """), {'cid': conversation_id, 'cm': (customer_msg or '')[:2000],
               'br': (bot_reply or '')[:2000], 'intent': intent,
               'outcome': outcome, 'notes': notes})

        conn.execute(text("""
            INSERT INTO cs_intent_patterns (intent, example_count, escalation_count,
                                             sample_questions, last_seen)
            VALUES (:intent, 1, :esc, CAST(:sample AS jsonb), NOW())
            ON CONFLICT (intent) DO UPDATE SET
              example_count = cs_intent_patterns.example_count + 1,
              escalation_count = cs_intent_patterns.escalation_count + :esc,
              sample_questions = (
                SELECT jsonb_agg(elem) FROM (
                  SELECT elem, ROW_NUMBER() OVER () AS rn, COUNT(*) OVER () AS total
                  FROM jsonb_array_elements_text(
                    cs_intent_patterns.sample_questions || CAST(:sample AS jsonb)
                  ) AS elem
                ) ranked WHERE rn > total - 10
              ),
              last_seen = NOW(),
              updated_at = NOW()
        """), {'intent': intent, 'esc': 1 if outcome == 'escalated' else 0,
               'sample': json.dumps([(customer_msg or '')[:200]])})


def _distill_memories(conn, all_classified_notes):
    notable = [c for c in all_classified_notes if c.get('notable_issue')]
    if not notable:
        return 0
    payload = json.dumps(notable[:60], ensure_ascii=False)
    try:
        resp = client.messages.create(
            model=config.TEXT_MODEL, max_tokens=800,
            system=_DISTILL_PROMPT.format(max_memories=MAX_MEMORIES),
            messages=[{'role': 'user', 'content': payload}],
        )
        raw = ''.join(b.text for b in resp.content if b.type == 'text')
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Distillation call failed: {e}', flush=True)
        return 0

    memories = _parse_json_array(raw)
    written = 0
    for m in memories[:MAX_MEMORIES]:
        text_val = (m.get('memory_text') or '').strip()
        if not text_val:
            continue
        category = (m.get('category') or 'other')[:50]
        importance = max(1, min(5, int(m.get('importance') or 1)))
        existing = conn.execute(text(
            "SELECT id FROM cs_agent_memory WHERE memory_text = :text"
        ), {'text': text_val[:500]}).fetchone()
        if existing:
            conn.execute(text(
                "UPDATE cs_agent_memory SET last_reinforced = NOW(), "
                "importance = GREATEST(importance, :imp) WHERE id = :id"
            ), {'imp': importance, 'id': existing._mapping['id']})
        else:
            conn.execute(text("""
                INSERT INTO cs_agent_memory (memory_text, category, importance)
                VALUES (:text, :cat, :imp)
            """), {'text': text_val[:500], 'cat': category, 'imp': importance})
        written += 1

    # Prune to the top MAX_MEMORIES * 3 by importance + recency so the table
    # (and the prompt it feeds) doesn't grow forever.
    conn.execute(text(f"""
        DELETE FROM cs_agent_memory WHERE id NOT IN (
            SELECT id FROM cs_agent_memory
            ORDER BY importance DESC, last_reinforced DESC
            LIMIT {MAX_MEMORIES * 3}
        )
    """))
    return written


def run():
    outcomes_written = memories_written = conversations_processed = 0
    with get_connection() as conn:
        conversations = _fetch_unlearned_conversations(conn)
        print(f"[cs_learner] {len(conversations)} conversations to learn from")

        all_notes = []
        for conv_id, messages in conversations:
            pairs = _pair_turns(messages)
            classified = _classify_conversation(pairs)
            if classified:
                _record_outcomes(conn, conv_id, pairs, classified)
                outcomes_written += len(classified)
                all_notes.extend(classified)
            conn.execute(text(
                "UPDATE cs_conversations SET learned_at = NOW() WHERE id = :id"
            ), {'id': conv_id})
            conversations_processed += 1
        conn.commit()

        memories_written = _distill_memories(conn, all_notes)
        conn.commit()

    msg = (f"cs_learner: {conversations_processed} conversations, "
           f"{outcomes_written} turns classified, {memories_written} memories written")
    print(f"[cs_learner] {msg}")
    return {'conversations': conversations_processed, 'outcomes': outcomes_written,
            'memories': memories_written}


if __name__ == '__main__':
    run()
