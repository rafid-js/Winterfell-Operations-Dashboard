"""Human handoff — flag the conversation, stop the bot, and alert the team.

Assignment is round-robin across Tumpa / Nafiz / Ayon (stable per customer so a
returning customer keeps the same owner). Reuses the existing telegram_alert
helper; the assignee and Rafid both get pinged.
"""
import sys
import os

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_alert  # noqa: E402

from . import config


def _pick_assignee(customer_id):
    """Deterministic round-robin so the same customer routes to the same agent."""
    configured = [(name, cid) for name, cid in config.TEAM_TELEGRAM_IDS if cid]
    if not configured:
        return (None, None)
    idx = sum(ord(c) for c in str(customer_id)) % len(configured)
    return configured[idx]


def trigger_handoff(conn, session, reason, message=''):
    """Mark the session handed off, notify the team, and log it. Idempotent-ish:
    re-flagging an already handed-off session just re-sends the alert."""
    session['status'] = 'handed_off'
    assignee_name, assignee_chat = _pick_assignee(session['customer_id'])
    session['handed_off_to'] = assignee_name

    channel = session.get('channel', 'unknown')
    customer = session.get('customer_name') or session.get('customer_id')

    alert = (
        "🚨 CS Handoff Required\n\n"
        f"👤 Customer: {customer}\n"
        f"📱 Channel: {channel.upper()}\n"
        f"❓ Reason: {reason}\n"
        f"💬 Last message: {(message or 'N/A')[:200]}\n\n"
        f"Assigned to: {assignee_name or 'UNASSIGNED'}"
    )

    if assignee_chat:
        telegram_alert.send(alert, bot_token=config.CS_TELEGRAM_BOT_TOKEN, chat_id=assignee_chat)
    if config.RAFID_TELEGRAM_ID:
        telegram_alert.send(alert, bot_token=config.CS_TELEGRAM_BOT_TOKEN,
                            chat_id=config.RAFID_TELEGRAM_ID)
    if not assignee_chat and not config.RAFID_TELEGRAM_ID:
        print(f'  ⚠ Handoff for {customer} but no Telegram IDs configured.\n{alert}', flush=True)

    try:
        conn.execute(text("""
            INSERT INTO cs_handoffs (conversation_id, reason, assigned_to, last_message)
            VALUES (:cid, :reason, :assignee, :msg)
        """), {'cid': session['id'], 'reason': reason,
               'assignee': assignee_name, 'msg': (message or '')[:1000]})
        conn.commit()
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Failed to log handoff: {e}', flush=True)
