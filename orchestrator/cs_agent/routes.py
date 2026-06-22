"""Webhook endpoints for Facebook, Instagram and WhatsApp (WATI).

GET  /webhook/<facebook|instagram>  — Meta webhook verification handshake.
POST /webhook/<channel>             — inbound messages.

Inbound messages are acknowledged with 200 immediately and processed on a
background thread (Claude calls take seconds; Meta retries if we're slow). This
mirrors the existing app's threaded cron-run pattern.
"""
import hmac
import hashlib
import threading

from flask import request, jsonify, Response

from . import cs_bp, config
from .router import process_message


def _spawn(channel, messages):
    for msg in messages:
        threading.Thread(target=_safe_process, args=(channel, msg), daemon=True).start()


def _safe_process(channel, msg):
    try:
        process_message(channel, msg)
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ CS process_message crashed ({channel}): {e}', flush=True)


def _verify_meta_signature(req) -> bool:
    """Validate X-Hub-Signature-256 when FB_APP_SECRET is configured."""
    if not config.FB_APP_SECRET:
        return True  # not configured — skip (dev / not yet set up)
    sig = req.headers.get('X-Hub-Signature-256', '')
    if not sig.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        config.FB_APP_SECRET.encode(), req.get_data(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ── Meta verification handshake (FB + IG share the flow) ────────────────────
def _verify_challenge():
    if request.args.get('hub.verify_token') == config.FB_VERIFY_TOKEN:
        return Response(request.args.get('hub.challenge', ''), mimetype='text/plain')
    return Response('Forbidden', status=403)


@cs_bp.route('/webhook/facebook', methods=['GET'])
def fb_verify():
    return _verify_challenge()


@cs_bp.route('/webhook/instagram', methods=['GET'])
def ig_verify():
    return _verify_challenge()


# ── Inbound messages ────────────────────────────────────────────────────────
def _handle_meta(channel):
    if not _verify_meta_signature(request):
        return jsonify({'error': 'bad signature'}), 403
    body = request.get_json(silent=True) or {}
    for entry in body.get('entry', []):
        for messaging in entry.get('messaging', []):
            # Skip echoes, delivery receipts, and read receipts.
            if messaging.get('message', {}).get('is_echo'):
                continue
            if 'message' not in messaging:
                continue
            _spawn(channel, [messaging])
    return jsonify({'status': 'ok'})


@cs_bp.route('/webhook/facebook', methods=['POST'])
def fb_webhook():
    return _handle_meta('facebook')


@cs_bp.route('/webhook/instagram', methods=['POST'])
def ig_webhook():
    return _handle_meta('instagram')


@cs_bp.route('/webhook/whatsapp', methods=['POST'])
def wa_webhook():
    body = request.get_json(silent=True) or {}
    # WATI posts a single message object per call.
    _spawn('whatsapp', [body])
    return jsonify({'status': 'ok'})
