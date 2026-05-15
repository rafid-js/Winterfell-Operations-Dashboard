"""
status_sync.py
Polls Nuport every 15 minutes for status changes on all orders we have pushed,
then updates the corresponding WooCommerce orders.

How tracking works:
  - When push_to_woocommerce.py creates a WC order, it saves the mapping:
      SO-XXXXX → WC order ID
    to tracked_orders.json
  - This script reads that file, polls Nuport for each SO, and syncs statuses.
  - Orders in terminal WC states (completed/cancelled/refunded) are skipped.

Run:  python status_sync.py
Or:   start_status_sync.bat (Windows, double-click)
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
    save_tracked_orders,
    setup_logging,
)

TERMINAL_WC_STATUSES = {"completed", "cancelled", "refunded"}


# ── Nuport API ────────────────────────────────────────────────────────────────

def fetch_nuport_order(so_number: str) -> dict | None:
    """
    GET /integration/orders/{so_number}
    Returns the order dict or None on failure.
    """
    cfg = get_config()
    url = f"{cfg['nuport_base_url']}/integration/orders/{so_number}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": cfg["nuport_api_key"]},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning(f"Failed to fetch Nuport order {so_number}: {exc}")
        return None


# ── WooCommerce API ───────────────────────────────────────────────────────────

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


# ── Sync cycle ────────────────────────────────────────────────────────────────

def sync_cycle():
    logging.info("── Status sync cycle starting ──")
    tracked = load_tracked_orders()

    if not tracked:
        logging.info("No tracked orders.")
        return

    updated = dict(tracked)
    changed = 0
    skipped = 0
    errors = 0

    for so_number, info in tracked.items():
        wc_order_id = info.get("wc_order_id")
        last_wc_status = info.get("last_wc_status", "")

        if last_wc_status in TERMINAL_WC_STATUSES:
            skipped += 1
            continue

        nuport_order = fetch_nuport_order(so_number)
        if not nuport_order:
            errors += 1
            continue

        nuport_status = nuport_order.get("status", "")
        new_wc_status = map_status(nuport_status)

        if new_wc_status == last_wc_status:
            continue  # No change

        if update_wc_status(wc_order_id, new_wc_status, so_number):
            updated[so_number]["last_wc_status"] = new_wc_status
            updated[so_number]["last_synced_at"] = datetime.now(timezone.utc).isoformat()
            changed += 1
        else:
            errors += 1

        time.sleep(0.3)  # Gentle rate limiting between WC API calls

    save_tracked_orders(updated)
    logging.info(
        f"── Sync complete: {changed} updated, {skipped} terminal (skipped), {errors} errors ──"
    )


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler():
    setup_logging()
    cfg = get_config()
    interval = cfg.get("status_sync_interval_minutes", 15)
    logging.info(f"Status sync scheduler started — interval: every {interval} minutes")

    sync_cycle()  # Run immediately on start

    schedule.every(interval).minutes.do(sync_cycle)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_scheduler()
