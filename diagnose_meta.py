"""
diagnose_meta.py
Compares meta_data between a genuine WC customer order (that Nuport already imported)
and one of our Nuport-pushed orders. The extra keys on the WC customer order are what
Nuport sets after importing — we need to pre-set those when creating our orders.

Usage:
  python diagnose_meta.py

Edit the two order IDs at the bottom before running.
"""

import json
import requests
from requests.auth import HTTPBasicAuth
from push_to_woocommerce import get_config

def get_wc_order(order_id):
    cfg = get_config()
    auth = HTTPBasicAuth(cfg["woocommerce_consumer_key"], cfg["woocommerce_consumer_secret"])
    url = f"{cfg['woocommerce_url'].rstrip('/')}/wp-json/wc/v3/orders/{order_id}"
    resp = requests.get(url, auth=auth, timeout=10)
    resp.raise_for_status()
    return resp.json()

def show_meta(order_id, label):
    print(f"\n{'='*60}")
    print(f"WC Order #{order_id} — {label}")
    print(f"{'='*60}")
    order = get_wc_order(order_id)
    print(f"  created_via : {order.get('created_via')}")
    print(f"  status      : {order.get('status')}")
    print(f"  customer_note: {order.get('customer_note', '')[:80]}")
    print(f"\n  meta_data ({len(order.get('meta_data', []))} keys):")
    for m in order.get("meta_data", []):
        key = m.get("key", "")
        val = str(m.get("value", ""))[:80]
        print(f"    {key:45s} = {val}")
    return set(m["key"] for m in order.get("meta_data", []))

if __name__ == "__main__":
    # ── EDIT THESE TWO VALUES ─────────────────────────────────────────────────
    # A real customer checkout order that Nuport has already imported into Nuport
    WC_CUSTOMER_ORDER = 76106   # e.g. the order that became SO-65778 in Nuport

    # One of our Nuport-pushed orders
    WC_NUPORT_PUSHED  = 76113   # e.g. the order we created from SO-65785
    # ─────────────────────────────────────────────────────────────────────────

    keys_customer = show_meta(WC_CUSTOMER_ORDER, "REAL WC ORDER (Nuport already imported this)")
    keys_pushed   = show_meta(WC_NUPORT_PUSHED,  "OUR PUSHED ORDER (Nuport sees as new)")

    print(f"\n{'='*60}")
    print("META KEYS ONLY ON THE CUSTOMER ORDER (set by Nuport after importing):")
    print("These are the keys we need to pre-set on our pushed orders.")
    print(f"{'='*60}")
    extra = keys_customer - keys_pushed
    if extra:
        for k in sorted(extra):
            print(f"  >> {k}")
    else:
        print("  No extra keys found — Nuport does NOT set meta on WC orders.")
        print("  The deduplication is purely internal to Nuport's database.")
        print("  We need a different approach (see below).")
