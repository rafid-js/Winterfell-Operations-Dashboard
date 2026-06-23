"""
Customer Support Agent — self-learning schema migration.

Adds the 4 learning tables deferred from the core CS agent build:

  cs_reply_outcomes  — per-turn classification (intent + outcome) of past
                       conversations, written by nightly_learner.
  cs_intent_patterns — rolling aggregate of how often each intent comes up
                       and how often it escalates, with sample questions.
  cs_knowledge_base  — best-known answer per intent, refined as resolved
                       turns accumulate. Distinct from the existing
                       knowledge_base table (that one is general business
                       documents; this one is CS Q&A pairs).
  cs_agent_memory    — a small, curated set of notes injected into the CS
                       agent's system prompt. Capped and pruned nightly so
                       the prompt stays short.

Also adds cs_conversations.learned_at so the nightly job only processes each
conversation once.

Safe to re-run — uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

Run once from brain/:
  python -m sync.cs_learning_setup
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection  # noqa: E402


CS_REPLY_OUTCOMES_SQL = """
CREATE TABLE IF NOT EXISTS cs_reply_outcomes (
    id                SERIAL PRIMARY KEY,
    conversation_id   INTEGER REFERENCES cs_conversations(id),
    customer_message  TEXT,
    bot_reply         TEXT,
    intent            VARCHAR(50),
    outcome           VARCHAR(20),
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT NOW()
);
"""

CS_INTENT_PATTERNS_SQL = """
CREATE TABLE IF NOT EXISTS cs_intent_patterns (
    intent            VARCHAR(50) PRIMARY KEY,
    example_count     INTEGER DEFAULT 0,
    escalation_count  INTEGER DEFAULT 0,
    sample_questions  JSONB DEFAULT '[]',
    last_seen         TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT NOW()
);
"""

CS_KNOWLEDGE_BASE_SQL = """
CREATE TABLE IF NOT EXISTS cs_knowledge_base (
    id                SERIAL PRIMARY KEY,
    intent            VARCHAR(50),
    question_pattern  TEXT,
    answer            TEXT,
    confidence        NUMERIC(3,2) DEFAULT 0.50,
    source            VARCHAR(20) DEFAULT 'learned',
    is_active         BOOLEAN DEFAULT TRUE,
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW()
);
"""

CS_AGENT_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS cs_agent_memory (
    id                SERIAL PRIMARY KEY,
    memory_text       TEXT NOT NULL,
    category          VARCHAR(50),
    importance        INTEGER DEFAULT 1,
    last_reinforced   TIMESTAMP DEFAULT NOW(),
    created_at        TIMESTAMP DEFAULT NOW()
);
"""

ADD_LEARNED_AT_SQL = """
ALTER TABLE cs_conversations ADD COLUMN IF NOT EXISTS learned_at TIMESTAMP;
"""

# cs_index was added in the core build but never seeded; cs_learner is new here.
SYSTEM_STATUS_SQL = """
INSERT INTO system_status (script_name, display_name, schedule) VALUES
    ('cs_index',   'CS product indexer',     'Nightly'),
    ('cs_learner', 'CS conversation learner', 'Nightly')
ON CONFLICT (script_name) DO NOTHING;
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_cro_conv ON cs_reply_outcomes(conversation_id);",
    "CREATE INDEX IF NOT EXISTS idx_cro_intent ON cs_reply_outcomes(intent);",
    "CREATE INDEX IF NOT EXISTS idx_ckb_intent ON cs_knowledge_base(intent);",
    "CREATE INDEX IF NOT EXISTS idx_ckb_active ON cs_knowledge_base(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_cam_importance ON cs_agent_memory(importance DESC, last_reinforced DESC);",
    "CREATE INDEX IF NOT EXISTS idx_cs_conv_learned ON cs_conversations(learned_at);",
]


def run():
    with get_connection() as conn:
        print("Creating cs_reply_outcomes ...")
        conn.execute(text(CS_REPLY_OUTCOMES_SQL))
        print("Creating cs_intent_patterns ...")
        conn.execute(text(CS_INTENT_PATTERNS_SQL))
        print("Creating cs_knowledge_base ...")
        conn.execute(text(CS_KNOWLEDGE_BASE_SQL))
        print("Creating cs_agent_memory ...")
        conn.execute(text(CS_AGENT_MEMORY_SQL))
        print("Adding cs_conversations.learned_at ...")
        conn.execute(text(ADD_LEARNED_AT_SQL))
        print("Seeding system_status ...")
        conn.execute(text(SYSTEM_STATUS_SQL))

        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ Index skipped: {e}")

        conn.commit()
    print("CS learning migration complete.")


if __name__ == '__main__':
    run()
