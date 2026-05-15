"""
order_poller.py
Polls Nuport every 5 minutes for new off-channel orders and pushes them to WooCommerce.
Also syncs status changes on already-pushed orders every 15 minutes.

This replaces the webhook approach — no ngrok, no public URL needed.
Nuport does not support outgoing webhooks to custom URLs, so polling is the correct method.

Run:  python order_poller.py
Or:   start_poller.bat (Windows, double-click)

How it works:
  1. Every 5 minutes: fetch recent Nuport orders
  2. Skip WEBSITE-source orders (already in WooCommerce)
  3. Skip orders already in tracked_orders.json (previously pushed)
  4. Push new off-channel orders to WooCommerce
  5. Every 15 minutes: sync status changes for all tracked orders
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import schedule

from push_to_woocommerce import (
    get_config,
    load_tracked_orders,
    map_status,
    process_order,
    retry_failed_orders,
    save_tracked_orders,
    setup_logging,
)

STATE_FILE = Path(__file__).parent / "poller_state.json"
TERMINAL_WC_STATUSES = {"completed", "cancelled", "refunded"}


# ── State helpers (tracks last seen shortId to avoid re-processing old orders) ─

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_short_id": 0, "last_poll_at": None}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Nuport API helpers ────────────────────────────────────────────────────────

def nuport_headers() -> dict:
    return {"Authorization": get_config()["nuport_api_key"]}


def fetch_nuport_orders(page: int = 0, page_size: int = 50) -> list:
    """
    GET /integration/orders — paginated list of recent orders.
    Returns list of order dicts, or empty list on failure.
    """
    cfg = get_config()
    url = f"{cfg['nuport_base_url']}/integration/orders"
    try:
        resp = requests.get(
            url,
            headers=nuport_headers(),
            params={"page": page, "pageSize": page_size},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # API may return {results: [...], count: N} or a plain list
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or data.get("orders") or data.get("data") or []
    except Exception as exc:
        logging.warning(f"Failed to fetch Nuport orders list: {exc}")
    return []


def fetch_nuport_order_by_id(so_number: str) -> dict | None:
    cfg = get_config()
    url = f"{cfg['nuport_base_url']}/integration/orders/{so_number}"
    try:
        resp = requests.get(url, headers=nuport_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning(f"Failed to fetch Nuport order {so_number}: {exc}")
    return None


# ── WooCommerce status update ─────────────────────────────────────────────────

def update_wc_status(wc_order_id: int, new_status: str, so_number: str) -> bool:
    from push_to_woocommerce import _wc_auth, _wc_url
    try:
        resp = requests.put(
            _wc_url(f"/orders/{wc_order_id}"),
            json={"status": new_status},
            auth=_wc_auth(),
            timeout=15,
        )
        resp.raise_for_status()
        logging.info(f"STATUS {so_number}: WC #{wc_order_id} → '{new_status}'")
        return True
    except Exception as exc:
        logging.error(f"Failed to update WC order #{wc_order_id} ({so_number}): {exc}")
        return False


# ── Poll for new orders ───────────────────────────────────────────────────────

def poll_new_orders():
    logging.info("── Polling Nuport for new orders ──")
    state = load_state()
    last_short_id = state.get("last_short_id", 0)
    tracked = load_tracked_orders()

    orders = fetch_nuport_orders(page=0, page_size=50)
    if not orders:
        logging.info("No orders returned from Nuport (or list endpoint unavailable).")
        return

    # Sort ascending by shortId so we process oldest-first
    orders_sorted = sorted(orders, key=lambda o: o.get("shortId", 0))

    new_max_id = last_short_id
    pushed = 0
    skipped = 0

    for order in orders_sorted:
        short_id = order.get("shortId", 0)
        so_number = order.get("internalId", "?")

        # Already tracked or already processed in a previous poll cycle
        if so_number in tracked:
            skipped += 1
            continue

        # Only process orders newer than last seen (on first run, processes all recent)
        if short_id <= last_short_id:
            skipped += 1
            continue

        result = process_order(order)
        if result:
            pushed += 1

        if short_id > new_max_id:
            new_max_id = short_id

        time.sleep(0.3)  # Gentle rate limiting

    state["last_short_id"] = new_max_id
    state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    retry_failed_orders()
    logging.info(f"── Poll complete: {pushed} pushed, {skipped} skipped ──")


# ── Sync status changes on tracked orders ────────────────────────────────────

def sync_statuses():
    logging.info("── Syncing status changes ──")
    tracked = load_tracked_orders()
    if not tracked:
        return

    updated = dict(tracked)
    changed = 0

    for so_number, info in tracked.items():
        wc_order_id = info.get("wc_order_id")
        last_wc_status = info.get("last_wc_status", "")

        if last_wc_status in TERMINAL_WC_STATUSES:
            continue

        order = fetch_nuport_order_by_id(so_number)
        if not order:
            continue

        new_wc_status = map_status(order.get("status", ""))
        if new_wc_status != last_wc_status:
            if update_wc_status(wc_order_id, new_wc_status, so_number):
                updated[so_number]["last_wc_status"] = new_wc_status
                updated[so_number]["last_synced_at"] = datetime.now(timezone.utc).isoformat()
                changed += 1

        time.sleep(0.3)

    save_tracked_orders(updated)
    logging.info(f"── Status sync complete: {changed} updated ──")


# ── Combined cycle ────────────────────────────────────────────────────────────

_sync_counter = 0

def poll_cycle():
    """Runs every 5 minutes. Also triggers status sync every 3rd run (15 min)."""
    global _sync_counter
    _sync_counter += 1
    poll_new_orders()
    if _sync_counter % 3 == 0:
        sync_statuses()


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run():
    setup_logging()
    cfg = get_config()
    interval = cfg.get("poll_interval_minutes", 5)

    logging.info("=" * 60)
    logging.info("Winterfell Nuport→WooCommerce order poller started")
    logging.info(f"Polling every {interval} minutes for new off-channel orders")
    logging.info("Press Ctrl+C to stop")
    logging.info("=" * 60)

    # Run immediately on start
    poll_cycle()

    schedule.every(interval).minutes.do(poll_cycle)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run()
