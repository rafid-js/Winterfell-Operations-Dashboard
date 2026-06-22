"""Conversation session store — backed by cs_conversations.

One active row per (channel, customer_id). Persists the rolling message history
and any pending image-match confirmation across webhook calls (webhooks are
stateless and may land on any worker, so an in-memory dict won't do).
"""
import json

from sqlalchemy import text


def _coerce(value, default):
    """JSONB columns come back as parsed objects on psycopg2, but tolerate str."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value


def get_or_create_session(conn, channel, customer_id, customer_name=None):
    """Fetch the active conversation for this customer, creating one if needed.
    Returns a dict with id, channel, customer_id, customer_name, messages,
    status, handed_off_to, pending_confirmation."""
    row = conn.execute(text("""
        SELECT id, customer_name, messages, status, handed_off_to, pending_confirmation
        FROM cs_conversations
        WHERE channel = :ch AND customer_id = :cid AND status <> 'resolved'
        ORDER BY id DESC LIMIT 1
    """), {'ch': channel, 'cid': str(customer_id)}).fetchone()

    if row:
        m = row._mapping
        # Backfill a name we didn't have at first contact.
        if customer_name and not m['customer_name']:
            conn.execute(text(
                "UPDATE cs_conversations SET customer_name = :n, updated_at = NOW() WHERE id = :id"
            ), {'n': customer_name, 'id': m['id']})
        return {
            'id': m['id'],
            'channel': channel,
            'customer_id': str(customer_id),
            'customer_name': customer_name or m['customer_name'],
            'messages': _coerce(m['messages'], []),
            'status': m['status'],
            'handed_off_to': m['handed_off_to'],
            'pending_confirmation': _coerce(m['pending_confirmation'], None),
        }

    new_id = conn.execute(text("""
        INSERT INTO cs_conversations (channel, customer_id, customer_name)
        VALUES (:ch, :cid, :n) RETURNING id
    """), {'ch': channel, 'cid': str(customer_id), 'n': customer_name}).scalar()
    conn.commit()
    return {
        'id': new_id, 'channel': channel, 'customer_id': str(customer_id),
        'customer_name': customer_name, 'messages': [], 'status': 'active',
        'handed_off_to': None, 'pending_confirmation': None,
    }


def save_session(conn, session):
    """Persist mutable session fields back to the row."""
    conn.execute(text("""
        UPDATE cs_conversations SET
          messages = CAST(:msgs AS jsonb),
          status = :status,
          handed_off_to = :handoff,
          pending_confirmation = CAST(:pending AS jsonb),
          updated_at = NOW()
        WHERE id = :id
    """), {
        'msgs': json.dumps(session.get('messages', [])),
        'status': session.get('status', 'active'),
        'handoff': session.get('handed_off_to'),
        'pending': json.dumps(session.get('pending_confirmation'))
                   if session.get('pending_confirmation') is not None else None,
        'id': session['id'],
    })
    conn.commit()


def append_turn(session, user_text, assistant_text):
    """Append a user/assistant exchange, capping history at the last 20 entries."""
    msgs = session.setdefault('messages', [])
    if user_text:
        msgs.append({'role': 'user', 'content': user_text})
    if assistant_text:
        msgs.append({'role': 'assistant', 'content': assistant_text})
    session['messages'] = msgs[-20:]
