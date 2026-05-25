"""
Run this ONCE to see exactly what Nuport returns.
Output tells us the field names so nuport_sync.py can map them correctly.

Usage:
  python nuport_explore.py                  # explore list endpoint
  python nuport_explore.py SO-64662         # explore a specific order
"""
import sys
import json
from apis.nuport import nuport


def pretty(data, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)
    print(json.dumps(data, indent=2, default=str)[:4000])
    print()


def explore_list():
    print("Fetching first page of orders from Nuport list endpoint...")
    try:
        data = nuport.list_orders(page=1, limit=5)
        pretty(data, "LIST ENDPOINT — raw response (first 5)")

        if isinstance(data, list):
            orders = data
        else:
            orders = data.get('data') or data.get('orders') or data.get('results') or []

        if orders:
            print(f"Found {len(orders)} orders in first page.")
            print("\nField names on first order:")
            for k, v in orders[0].items():
                print(f"  {k}: {repr(v)[:80]}")
        else:
            print("List endpoint returned no orders or unexpected structure.")
            print("Full raw response printed above — share it and we'll adjust the mapper.")

    except Exception as e:
        print(f"✗ List endpoint failed: {e}")
        print("  → Nuport may not support listing without filters.")
        print("  → Try: python nuport_explore.py SO-XXXXX with a known order number.")


def explore_order(order_id):
    print(f"Fetching single order: {order_id}")
    try:
        data = nuport.get_order(order_id)
        pretty(data, f"SINGLE ORDER — {order_id}")
        print("Top-level field names:")
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                print(f"  {k}: [{type(v).__name__}] {str(v)[:80]}")
            else:
                print(f"  {k}: {repr(v)}")
    except Exception as e:
        print(f"✗ Failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        explore_order(sys.argv[1])
    else:
        explore_list()
