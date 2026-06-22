"""Text path — reply with Claude (Haiku) using the customer's order context.

Also resolves a pending image-match confirmation: if the previous turn asked
"is this the X?" and the customer says yes, we re-check stock for that SKU.
"""
import re

from sqlalchemy import text

from . import config, memory
from .claude_client import client
from .reply import send_reply
from .handoff import trigger_handoff

_YES_WORDS = ['হ্যাঁ', 'হা', 'হ্যা', 'ha', 'haa', 'haan', 'yes', 'yep', 'ji', 'জি']

# Claude's stock-graceful handoff sentinel (kept in sync with the system prompt).
_HANDOFF_SENTINEL = 'team handle করবে'


def _stock_line(stock_json):
    parts = [f"{s} ({int(stock_json.get(s) or 0)}টা)"
             for s in config.SIZES if int(stock_json.get(s) or 0) > 0]
    return ' · '.join(parts) if parts else None


def _order_context(conn, channel, customer_id):
    """Recent orders for this customer. Only resolvable when we have a phone —
    WhatsApp's customer_id IS the phone (waId); FB/IG have no phone mapping."""
    if channel != 'whatsapp':
        return ''
    digits = re.sub(r'\D', '', str(customer_id))
    if len(digits) < 6:
        return ''
    rows = conn.execute(text("""
        SELECT so_number,
               COALESCE(wc_status, nuport_status, 'Processing') AS status,
               order_date
        FROM orders
        WHERE RIGHT(regexp_replace(COALESCE(customer_phone, ''), '\\D', '', 'g'), 10)
              = RIGHT(:digits, 10)
        ORDER BY order_date DESC NULLS LAST
        LIMIT 5
    """), {'digits': digits}).fetchall()
    if not rows:
        return ''
    lines = ["Customer's recent orders:"]
    for r in rows:
        m = r._mapping
        when = m['order_date'].strftime('%d %b') if m['order_date'] else '—'
        lines.append(f"- {m['so_number']}: {m['status']} ({when})")
    return '\n'.join(lines)


def _system_prompt(order_context):
    return (
        "You are Winterfell's customer support agent. Winterfell is a Gen Z "
        "fast-fashion brand in Bangladesh (sizes M–3XL only).\n"
        "Reply in a natural Bangla/English mix — friendly, warm, efficient. Use "
        "\"ভাই\" for males, \"আপু\" if clearly female. Keep replies SHORT (2–4 lines).\n\n"
        + (order_context + "\n\n" if order_context else "No previous orders found for this customer.\n\n")
        + "You CAN answer: stock availability, price, size guidance (M≈38-40\", "
        "L≈40-42\", XL≈42-44\", XXL≈44-46\", 3XL≈46-48\" chest), order status (use the "
        "orders above), delivery time (Dhaka 1–2 days, outside Dhaka 2–3 days via "
        "Pathao), and general product questions.\n"
        "You CANNOT handle refunds, returns, exchanges, payment disputes, or "
        f"complaints — for those reply EXACTLY: \"এটা আমাদের {_HANDOFF_SENTINEL}, একটু wait করুন 🙏\".\n"
        "Never invent stock numbers. If you don't know, say you'll check."
    )


def _resolve_pending_confirmation(conn, session, message):
    """Handle a yes/no answer to an earlier low-confidence image match."""
    pending = session.get('pending_confirmation')
    if not pending:
        return False
    lowered = message.lower()
    said_yes = any(w in lowered for w in _YES_WORDS)
    session['pending_confirmation'] = None  # consume it either way

    if not said_yes:
        reply = ("আচ্ছা ঠিক আছে ভাই 🙏 আপনি কোন product টা খুঁজছেন একটু বলবেন, "
                 "অথবা ছবি পাঠান — আমি check করে দিচ্ছি।")
        memory.append_turn(session, message, reply)
        send_reply(session['channel'], session['customer_id'], reply)
        return True

    row = conn.execute(text("""
        SELECT product_name, stock_json, price FROM product_embeddings
        WHERE product_name = :pn
    """), {'pn': pending.get('product_name')}).fetchone()
    if row:
        m = row._mapping
        stock = memory._coerce(m['stock_json'], {})
        line = _stock_line(stock)
        if line:
            reply = (f"✅ {m['product_name']} — ৳{int(m['price'] or 0)}\n"
                     f"Available: {line}\n\nকোন সাইজটা নিতে চাচ্ছেন?")
        else:
            reply = (f"{m['product_name']} এই মুহূর্তে stock এ নেই 😔 Restock হলে "
                     f"page এ update দেব ভাই 🙏")
    else:
        reply = "Sorry ভাই, ওই product টা এখন খুঁজে পাচ্ছি না 🙏 আমাদের team check করবে।"
    memory.append_turn(session, message, reply)
    send_reply(session['channel'], session['customer_id'], reply)
    return True


def handle_text(conn, session, message):
    from .reply import send_reply as _send  # local alias for clarity

    if _resolve_pending_confirmation(conn, session, message):
        return

    order_context = _order_context(conn, session['channel'], session['customer_id'])
    system_prompt = _system_prompt(order_context)

    history = [{'role': m['role'], 'content': m['content']}
               for m in session.get('messages', [])[-6:]]

    try:
        resp = client.messages.create(
            model=config.TEXT_MODEL,
            max_tokens=300,
            system=system_prompt,
            messages=history + [{'role': 'user', 'content': message}],
        )
        reply = ''.join(b.text for b in resp.content if b.type == 'text').strip()
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Claude text call failed: {e}', flush=True)
        reply = "একটু সমস্যা হচ্ছে ভাই 🙏 আমাদের team একটু পরে reply করবে।"
        trigger_handoff(conn, session, reason='Claude text call failed', message=message)
        memory.append_turn(session, message, reply)
        _send(session['channel'], session['customer_id'], reply)
        return

    memory.append_turn(session, message, reply)
    _send(session['channel'], session['customer_id'], reply)

    # Claude decided this needs a human — flag it after replying.
    if _HANDOFF_SENTINEL in reply:
        trigger_handoff(conn, session, reason='Claude routed to human', message=message)
