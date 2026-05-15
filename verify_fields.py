"""
verify_fields.py
One-shot script to confirm which Nuport field holds the WooCommerce order ID.

Run it with a real WEBSITE-sourced Nuport order that you know came from WooCommerce:
  python verify_fields.py --so-number SO-65778

It will print every top-level field so you can see which one holds the WC order ID (e.g. 76106).
Then update "website_order_id_field" in config.json accordingly.
"""

import argparse
import json
import sys

import requests

from push_to_woocommerce import get_config


def main():
    parser = argparse.ArgumentParser(description="Verify Nuport order field names")
    parser.add_argument("--so-number", required=True, help="Nuport SO number e.g. SO-65778")
    args = parser.parse_args()

    cfg = get_config()
    so = args.so_number.strip()
    url = f"{cfg['nuport_base_url']}/integration/orders/{so}"

    print(f"\nFetching Nuport order: {so}")
    print(f"URL: {url}\n")

    try:
        resp = requests.get(
            url,
            headers={"Authorization": cfg["nuport_api_key"]},
            timeout=10,
        )
        resp.raise_for_status()
        order = resp.json()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print("=" * 60)
    print("TOP-LEVEL FIELDS (look for the WooCommerce order ID here):")
    print("=" * 60)

    candidate_fields = [
        "integrationId", "referenceId", "transactionInternalId",
        "externalId", "websiteOrderId", "wcOrderId", "woocommerceId"
    ]

    for field in candidate_fields:
        value = order.get(field)
        marker = " ← CANDIDATE" if value else ""
        print(f"  {field:35s} = {repr(value)}{marker}")

    print()
    print("ALL top-level scalar fields:")
    for key, val in order.items():
        if not isinstance(val, (dict, list)):
            print(f"  {key:35s} = {repr(val)}")

    print()
    print("Source field (should be 'WEBSITE' for WC orders):", repr(order.get("source")))
    print()

    current_field = cfg.get("website_order_id_field", "integrationId")
    current_value = order.get(current_field)
    print(f"Current config 'website_order_id_field' = '{current_field}'")
    print(f"Value of that field on this order = {repr(current_value)}")

    if current_value:
        print(f"\n✓ '{current_field}' contains a value — your config looks correct.")
    else:
        print(f"\n✗ '{current_field}' is null/empty.")
        print("  Update 'website_order_id_field' in config.json to the correct field name.")


if __name__ == "__main__":
    main()
