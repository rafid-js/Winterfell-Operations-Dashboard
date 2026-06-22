"""Channel-specific reply senders: Facebook / Instagram (Graph API) and
WhatsApp (WATI). Each returns True on success, False otherwise — failures are
logged, never raised, so a send error can't crash the webhook handler.
"""
import requests

from . import config


def send_reply(channel, customer_id, message):
    if channel == 'facebook':
        return _send_messenger(customer_id, message, config.FB_PAGE_ACCESS_TOKEN)
    if channel == 'instagram':
        return _send_messenger(customer_id, message, config.IG_ACCESS_TOKEN)
    if channel == 'whatsapp':
        return _send_whatsapp(customer_id, message)
    print(f'  ⚠ Unknown channel for reply: {channel}', flush=True)
    return False


def _send_messenger(recipient_id, message, access_token):
    """Facebook Messenger & Instagram both use the Graph /me/messages endpoint."""
    if not access_token:
        print('  ⚠ No page/IG access token — Messenger reply skipped.', flush=True)
        return False
    try:
        r = requests.post(
            f'https://graph.facebook.com/{config.GRAPH_API_VERSION}/me/messages',
            params={'access_token': access_token},
            json={
                'recipient': {'id': recipient_id},
                'messaging_type': 'RESPONSE',
                'message': {'text': message},
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f'  ⚠ Messenger send failed: {r.status_code} {r.text}', flush=True)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Messenger send error: {e}', flush=True)
        return False


def _send_whatsapp(wa_id, message):
    """Send a WhatsApp session message via WATI's sendSessionMessage endpoint."""
    if not config.WATI_API_KEY or not config.WATI_ENDPOINT:
        print('  ⚠ WATI not configured — WhatsApp reply skipped.', flush=True)
        return False
    try:
        r = requests.post(
            f'{config.WATI_ENDPOINT}/api/v1/sendSessionMessage/{wa_id}',
            headers={'Authorization': f'Bearer {config.WATI_API_KEY}'},
            params={'messageText': message},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            print(f'  ⚠ WATI send failed: {r.status_code} {r.text}', flush=True)
        return r.status_code in (200, 201)
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ WATI send error: {e}', flush=True)
        return False
