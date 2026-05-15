"""
order_poller.py
Polls Nuport every 5 minutes for new off-channel orders and pushes them to WooCommerce.
Also syncs status changes on already-pushed orders every 15 minutes.

No webhook or ngrok needed. Nuport's public API has no list-orders endpoint,
so we walk forward from the last known shortId:
  SO-65778 (shortId 65778) → try SO-65779, SO-65780, … stop after 5 consecutive 404s.

FIRST RUN SETUP — add this to config.json:
  "start_from_short_id": 65778
  Set it to your current highest Nuport shortId so only NEW orders are pushed.
  Find it: open any recent Nuport order, the number after "SO-" is the shortId.
  Example: SO-65778 → set 65778.

Run:  python order_poller.py
Or:   start_poller.bat (Windows, double-click)
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


# ── State (tracks last seen shortId across restarts) ──────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def format_so(short_id: int) -> str:
    """Convert numeric shortId to Nuport SO number. SO-0036, SO-65778, etc."""
    return f"SO-{short_id:04d}"


# ── Nuport API ────────────────────────────────────────────────────────────────

def nuport_headers() -> dict:
    return {"Authorization": get_config()["nuport_api_key"]}


def fetch_nuport_order(so_number: str) -> dict | None:
    cfg = get_config()
    try:
        resp = requests.get(
            f"{cfg['nuport_base_url']}/integration/orders/{so_number}",
            headers=nuport_headers(),
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        # Confirm it's a real order (not an error body)
        if isinstance(data, dict) and data.get("internalId"):
            return data
        return None
    except requests.exceptions.HTTPError:
        return None
    except Exception as exc:
        logging.warning(f"Error fetching {so_number}: {exc}")
        return None


# ── Walk-forward order discovery ──────────────────────────────────────────────

def fetch_new_orders(from_short_id: int) -> tuple:
    """
    Walk forward from from_short_id+1, fetching each SO number.
    Stops after 5 consecutive 404s (handles small gaps in shortId sequence).
    Returns (list_of_new_orders, highest_short_id_seen).
    """
    orders = []
    current = from_short_id + 1
    max_seen = from_short_id
    consecutive_misses = 0
    max_consecutive_misses = 5

    while consecutive_misses < max_consecutive_misses:
        so = format_so(current)
        order = fetch_nuport_order(so)

        if order:
            orders.append(order)
            max_seen = current
            consecutive_misses = 0
            logging.debug(f"Found {so}")
        else:
            consecutive_misses += 1

        current += 1
        time.sleep(0.2)  # Gentle — don't hammer Nuport API

    return orders, max_seen


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
    logging.info("── Polling for new Nuport orders ──")
    cfg = get_config()
    state = load_state()

    # First run: seed from config so we don't push historical orders
    if "last_short_id" not in state:
        start_id = cfg.get("start_from_short_id", 0)
        if start_id == 0:
            logging.warning(
                "start_from_short_id not set in config.json! "
                "Add it to avoid pushing historical orders. "
                "Example: \"start_from_short_id\": 65778"
            )
        state["last_short_id"] = start_id
        state["initialized_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        logging.info(
            f"First run — starting from shortId {start_id}. "
            "Only orders after this will be pushed."
        )

    last_short_id = state["last_short_id"]
    logging.info(f"Scanning from shortId {last_short_id + 1}...")

    new_orders, new_max = fetch_new_orders(last_short_id)

    if not new_orders:
        logging.info("No new orders found.")
        # Still advance max_seen so we don't re-scan same range unnecessarily
        state["last_short_id"] = new_max
        state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    logging.info(f"Found {len(new_orders)} new order(s) to process.")
    pushed = 0
    for order in new_orders:
        if process_order(order):
            pushed += 1
        time.sleep(0.3)

    retry_failed_orders()

    state["last_short_id"] = new_max
    state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    logging.info(f"── Poll done: {pushed}/{len(new_orders)} pushed ──")


# ── Sync statuses on tracked orders ──────────────────────────────────────────

def sync_statuses():
    logging.info("── Syncing order statuses ──")
    tracked = load_tracked_orders()
    if not tracked:
        logging.info("No tracked orders to sync.")
        return

    updated = dict(tracked)
    changed = 0

    for so_number, info in tracked.items():
        wc_order_id = info.get("wc_order_id")
        last_wc_status = info.get("last_wc_status", "")

        if last_wc_status in TERMINAL_WC_STATUSES:
            continue

        order = fetch_nuport_order(so_number)
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
    logging.info(f"── Status sync done: {changed} updated ──")


# ── Scheduler ─────────────────────────────────────────────────────────────────

_cycle_count = 0


def poll_cycle():
    global _cycle_count
    _cycle_count += 1
    poll_new_orders()
    if _cycle_count % 3 == 0:   # Every 3rd poll = every 15 min at 5-min interval
        sync_statuses()


def run():
    setup_logging()
    cfg = get_config()
    interval = cfg.get("poll_interval_minutes", 5)

    logging.info("=" * 60)
    logging.info("Winterfell Nuport→WooCommerce order poller started")
    logging.info(f"New order check: every {interval} minutes")
    logging.info(f"Status sync:     every {interval * 3} minutes")
    logging.info("Press Ctrl+C to stop")
    logging.info("=" * 60)

    poll_cycle()  # Run immediately on start

    schedule.every(interval).minutes.do(poll_cycle)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run()
