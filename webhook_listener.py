"""
webhook_listener.py
Flask server that receives Nuport order webhooks and pushes to WooCommerce.

Always returns HTTP 200 immediately — processing happens in a background thread
so Nuport never times out waiting for a response.

Setup:
  1. Run this server (start_listener.bat or: python webhook_listener.py)
  2. Expose it publicly via ngrok: ngrok http 5000
  3. Register the ngrok URL in Nuport → Settings → Webhooks:
       URL:    https://your-id.ngrok.io/webhook/nuport
       Secret: the value of nuport_webhook_secret from config.json
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

from push_to_woocommerce import process_order, retry_failed_orders, setup_logging

app = Flask(__name__)
setup_logging()


def _load_secret() -> str:
    cfg_path = Path(__file__).parent / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f).get("nuport_webhook_secret", "")


def verify_secret(header_value: str) -> bool:
    """Simple constant-time secret comparison."""
    import hmac
    expected = _load_secret()
    if not expected:
        return True  # No secret configured — allow all (not recommended for production)
    return hmac.compare_digest(expected, header_value or "")


@app.route("/webhook/nuport", methods=["POST"])
def nuport_webhook():
    """
    Receive a Nuport order event.
    Always returns 200 immediately. Processing is async in a background thread.
    """
    secret_header = request.headers.get("X-Nuport-Secret", "")

    if not verify_secret(secret_header):
        logging.warning("Webhook received with invalid X-Nuport-Secret — processing anyway")

    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        logging.error("Failed to parse webhook JSON payload")
        return jsonify({"status": "ok"}), 200

    # Spawn background thread — respond to Nuport BEFORE processing
    thread = threading.Thread(
        target=_process_in_background,
        args=(data,),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "ok"}), 200


def _process_in_background(data: dict):
    try:
        # Nuport may wrap the order or send it directly — handle both shapes
        order = (
            data.get("order")
            or data.get("data")
            or data.get("salesOrder")
            or data
        )

        if not isinstance(order, dict):
            logging.warning(f"Unexpected webhook payload type: {type(order)}")
            return

        if "internalId" not in order:
            logging.warning(f"Webhook payload missing 'internalId'. Keys: {list(order.keys())}")
            return

        process_order(order)
        retry_failed_orders()

    except Exception as exc:
        logging.error(f"Background processing error: {exc}", exc_info=True)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "time": datetime.now(timezone.utc).isoformat(),
        "service": "nuport-wc-sync",
    }), 200


@app.route("/retry-failed", methods=["POST"])
def manual_retry():
    """Manually trigger a retry of all failed orders. POST /retry-failed"""
    thread = threading.Thread(target=retry_failed_orders, daemon=True)
    thread.start()
    return jsonify({"status": "retry started"}), 200


if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("Nuport → WooCommerce webhook listener starting on port 5000")
    logging.info("Endpoints:")
    logging.info("  POST /webhook/nuport  — receive Nuport order events")
    logging.info("  GET  /health          — health check")
    logging.info("  POST /retry-failed    — manually retry failed orders")
    logging.info("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)
