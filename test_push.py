"""
test_push.py
Run this BEFORE setting up any webhook to verify your config is working.

It simulates a WhatsApp off-channel order using the exact field structure
returned by the Nuport API (confirmed from live API docs), then pushes it
to WooCommerce.

Usage:
  python test_push.py
  python test_push.py --dry-run     (show payload without sending to WC)

What to check after running:
  1. No errors in the console output
  2. A new order appears in WooCommerce admin
  3. The order has these meta fields:
       _nuport_so_number    = SO-TEST99
       _nuport_order_source = WHATSAPP
       _nuport_synced_at    = <timestamp>
  4. Customer note reads: "Order via WHATSAPP | Nuport: SO-TEST99"
  5. Check logs/sync_<today>.log for detailed output
"""

import json
import sys
from push_to_woocommerce import (
    setup_logging,
    process_order,
    build_wc_order,
    check_should_push,
    normalise_phone,
    map_status,
)

# ── Simulated Nuport order (exact API field structure) ─────────────────────────
# This mirrors what the Nuport API actually returns for a real order.
# See: GET https://api.nuport.io/integration/orders/SO-TEST99

TEST_ORDER_WHATSAPP = {
    "statusCode": 200,
    "id": "test-uuid-0000-0000-whatsapp-test",
    "internalId": "SO-TEST99",          # SO number — used as duplicate-check key
    "source": "WHATSAPP",               # UPPERCASE — confirmed from API docs
    "integrationId": None,              # No WC order ID → this is truly off-channel
    "referenceId": None,
    "status": "PENDING",                # UPPERCASE status
    "deliveryCharge": "80",
    "totalAmount": "570",
    "district": "Narayanganj District",
    "division": "Dhaka Division",
    "distributor": {
        "id": "test-distributor-id",
        "name": "Zahid Mustafiz",       # Full name → split into first/last
        "phone": "+8801949644003",      # +880 format → normalised to 01949644003
        "email": "zahidmustafiz568@gmail.com",
        "type": "E_COMMERCE_CUSTOMER",
    },
    "location": {
        "id": "test-location-id",
        "address": "Friends garden, mizmizi, siddhirgonj, Narayanganj",
        "district": "Narayanganj District",
        "division": "Dhaka Division",
        "postCode": "1430",
        "country": "BD",
    },
    "salesOrderItems": [
        {
            "id": 9999,
            "quantity": 1,
            "price": "490",             # Product price (not including delivery)
            "product": {
                "id": "test-product-id",
                "name": "Hope Rose Tee - Black - 3XL",
                "sku": "54463-54468",   # SKU format confirmed from live screenshot
            },
        }
    ],
}

# Also test a WEBSITE order — should be SKIPPED
TEST_ORDER_WEBSITE = {
    "internalId": "SO-TEST100",
    "source": "WEBSITE",                # Should be skipped
    "integrationId": "76106",           # WC order ID already set
    "status": "APPROVED",
    "deliveryCharge": "0",
    "totalAmount": "490",
    "distributor": {"name": "Test Customer", "phone": "+8801700000000", "email": ""},
    "location": {"address": "Dhaka", "district": "Dhaka District", "postCode": ""},
    "salesOrderItems": [],
}

# Test a MESSENGER order
TEST_ORDER_MESSENGER = {
    "internalId": "SO-TEST101",
    "source": "MESSENGER",
    "integrationId": None,
    "status": "APPROVED",
    "deliveryCharge": "60",
    "totalAmount": "660",
    "district": "Dhaka District",
    "distributor": {"name": "Test Buyer Two", "phone": "+8801811111111", "email": ""},
    "location": {
        "address": "Mirpur-10, Dhaka",
        "district": "Dhaka District",
        "postCode": "1216",
        "country": "BD",
    },
    "salesOrderItems": [
        {
            "quantity": 2,
            "price": "300",
            "product": {"name": "Test Product No SKU", "sku": ""},
        }
    ],
}


def run_filter_tests():
    print("\n── Filter logic tests ──")
    cases = [
        (TEST_ORDER_WHATSAPP,  True,  "WhatsApp order should push"),
        (TEST_ORDER_WEBSITE,   False, "Website order should skip"),
        (TEST_ORDER_MESSENGER, True,  "Messenger order should push"),
    ]
    all_pass = True
    for order, expected_push, label in cases:
        should, reason = check_should_push(order)
        status = "PASS" if (should == expected_push) else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: {label}")
        print(f"       push={should}, reason={reason}")
    return all_pass


def run_mapping_tests():
    print("\n── Status mapping tests ──")
    cases = [
        ("PENDING",           "processing"),
        ("APPROVED",          "processing"),
        ("SHIPPED",           "processing"),
        ("ON_HOLD",           "on-hold"),
        ("COMPLETED",         "pending"),
        ("PAYMENT_COLLECTED", "completed"),
        ("CANCELLED",         "cancelled"),
        ("FLAGGED",           "refunded"),
        ("UNKNOWN_STATUS_XYZ","on-hold"),
    ]
    all_pass = True
    for nuport_status, expected_wc in cases:
        result = map_status(nuport_status)
        status = "PASS" if result == expected_wc else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: '{nuport_status}' → '{result}' (expected '{expected_wc}')")
    return all_pass


def run_phone_tests():
    print("\n── Phone normalisation tests ──")
    cases = [
        ("+8801949644003", "01949644003"),
        ("8801711000000",  "01711000000"),
        ("01700000000",    "01700000000"),
        ("",               ""),
    ]
    all_pass = True
    for raw, expected in cases:
        result = normalise_phone(raw)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: '{raw}' → '{result}' (expected '{expected}')")
    return all_pass


def show_payload():
    print("\n── WC order payload (dry run, WhatsApp order) ──")
    payload = build_wc_order(TEST_ORDER_WHATSAPP, customer_id=None)
    print(json.dumps(payload, indent=2))


def main():
    dry_run = "--dry-run" in sys.argv
    setup_logging()

    print("=" * 60)
    print("Winterfell Nuport→WooCommerce — Test Suite")
    print("=" * 60)

    f1 = run_filter_tests()
    f2 = run_mapping_tests()
    f3 = run_phone_tests()

    if not (f1 and f2 and f3):
        print("\n✗ Some logic tests FAILED — check output above before pushing live orders.")
        sys.exit(1)

    print("\n✓ All logic tests passed.")

    if dry_run:
        show_payload()
        print("\nDry run complete. No orders sent to WooCommerce.")
        return

    print("\n── Live push test: WhatsApp order ──")
    print(f"Order: {TEST_ORDER_WHATSAPP['internalId']}")
    print(f"Customer: {TEST_ORDER_WHATSAPP['distributor']['name']}")
    print(f"Source: {TEST_ORDER_WHATSAPP['source']}")
    print()

    result = process_order(TEST_ORDER_WHATSAPP)
    if result:
        print("✓ WhatsApp order pushed (or skipped as duplicate).")
    else:
        print("✗ WhatsApp order FAILED — check logs/ for details.")

    print("\n── Live push test: Website order (expect SKIP) ──")
    result2 = process_order(TEST_ORDER_WEBSITE)
    if result2:
        print("✓ Website order correctly skipped.")
    else:
        print("✗ Unexpected failure on Website order skip.")

    print("\nDone. Check logs/ for full details.")
    print("Check WooCommerce admin to confirm SO-TEST99 appeared as a new order.")


if __name__ == "__main__":
    main()
