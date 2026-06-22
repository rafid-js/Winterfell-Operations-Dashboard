"""Routing: parse a channel payload into (customer_id, name, text, image_url),
then dispatch to the text or image handler. Runs the heavy work (Claude calls)
on its own DB connection so webhook handlers can return 200 immediately from a
background thread.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'brain'))
from db import get_connection  # noqa: E402

from . import memory
from .handoff import trigger_handoff

# Anything in here goes straight to a human — never auto-answered.
HANDOFF_KEYWORDS = [
    'refund', 'return', 'complaint', 'problem', 'cancel', 'payment', 'paid',
    'ভুল', 'রিটার্ন', 'রিফান্ড', 'সমস্যা', 'ধোঁকা', 'অর্ডার বাতিল', 'পেমেন্ট',
    'bkash failed', 'বিকাশ',
]


def _extract(channel, msg):
    """Return (customer_id, customer_name, text, image_url) for one message."""
    if channel in ('facebook', 'instagram'):
        sender = (msg.get('sender') or {}).get('id')
        message = msg.get('message') or {}
        text_body = message.get('text')
        image_url = None
        for att in message.get('attachments') or []:
            if att.get('type') == 'image':
                image_url = (att.get('payload') or {}).get('url')
                break
        return sender, None, text_body, image_url

    if channel == 'whatsapp':
        # WATI webhook payload (defensive — exact keys depend on WATI config).
        wa_id = msg.get('waId') or msg.get('wa_id') or msg.get('phone')
        name = msg.get('senderName') or msg.get('name')
        mtype = (msg.get('type') or '').lower()
        text_body = msg.get('text')
        image_url = None
        if mtype == 'image' or msg.get('data'):
            image_url = msg.get('mediaUrl') or msg.get('data') or msg.get('url')
        return wa_id, name, text_body, image_url

    return None, None, None, None


def process_message(channel, msg):
    """Entry point for one inbound message. Opens its own connection."""
    customer_id, name, text_body, image_url = _extract(channel, msg)
    if not customer_id:
        return
    if not text_body and not image_url:
        return  # delivery receipt, reaction, etc. — nothing to answer

    with get_connection() as conn:
        session = memory.get_or_create_session(conn, channel, customer_id, name)

        # A human is already handling this conversation — stay quiet.
        if session['status'] == 'handed_off':
            return

        # Hard handoff keywords bypass the bot entirely.
        if text_body and any(kw in text_body.lower() for kw in HANDOFF_KEYWORDS):
            trigger_handoff(conn, session, reason='Handoff keyword detected', message=text_body)
            memory.append_turn(session, text_body, None)
            memory.save_session(conn, session)
            return

        try:
            if image_url:
                from .image_handler import handle_image
                handle_image(conn, session, image_url)
            else:
                from .text_handler import handle_text
                handle_text(conn, session, text_body)
        finally:
            memory.save_session(conn, session)
