"""
Clean up garbled data from the Nuport CSV import.

The CSV had column-shift errors on some rows, causing:
  - nuport_status to contain prices (200.00), weights (weight: 0.25 kg),
    order-number ranges (32143-32162), truncated text (BD"), etc.
  - order_items.product_name to contain prices instead of product names
  - so_number to contain prices, addresses, or dates instead of order IDs

Steps:
  1. Fix lowercase 'delivered' → 'DELIVERED'
  2. Normalize 'COMPLETED' → 'DELIVERED' (Nuport API renamed the status)
  3. Delete rows with garbled SO numbers (prices, addresses, dates)
  4. Null out garbled status values
  5. Fix garbled product names in order_items using skus table
  6. Re-fetch orders with NULL status from Nuport API

Run:
  python cleanup_garbled.py
"""
import sys
sys.path.insert(0, __file__.rsplit('\\', 1)[0])
sys.path.insert(0, __file__.rsplit('/', 1)[0])

from db import get_connection
from sqlalchemy import text


GARBLED_STATUS_PATTERN = r"""
    nuport_status ~ '^\d+(\.\d+)?$'         -- pure numbers / prices: 200.00, 590
 OR nuport_status ~ '^\d{4,}-\d+$'          -- order-number ranges: 32143-32162
 OR nuport_status ILIKE 'weight:%'           -- weight: 0.25 kg
 OR nuport_status ~ '^BD[^A-Z]'             -- truncated: BD"
"""

# Valid SO numbers start with SO-NNNNN or WIN-NNNNN (suffix like -FPR-R, -1PR, etc. is fine)
VALID_SO_PATTERN = r"^(SO|WIN)-\d+"


def normalize_completed(conn):
    n = conn.execute(text(
        "UPDATE orders SET nuport_status = 'DELIVERED' WHERE nuport_status = 'COMPLETED'"
    )).rowcount
    conn.commit()
    print(f"  ✓ Normalised 'COMPLETED' → 'DELIVERED': {n} rows")


def delete_garbled_so_numbers(conn):
    rows = conn.execute(text(f"""
        SELECT so_number, nuport_status FROM orders
        WHERE so_number !~ '{VALID_SO_PATTERN}'
        ORDER BY so_number
    """)).fetchall()
    print(f"\n  Rows with garbled SO numbers: {len(rows)}")
    for r in rows:
        print(f"    '{r[0]}'  status={r[1]}")

    if rows:
        # Delete order_items first (FK constraint), then orders
        n_items = conn.execute(text(f"""
            DELETE FROM order_items
            WHERE so_number IN (
                SELECT so_number FROM orders WHERE so_number !~ '{VALID_SO_PATTERN}'
            )
        """)).rowcount
        n = conn.execute(text(f"""
            DELETE FROM orders WHERE so_number !~ '{VALID_SO_PATTERN}'
        """)).rowcount
        conn.commit()
        print(f"  ✓ Deleted {n} orders and {n_items} order_items with garbled SO numbers")


def fix_statuses(conn):
    # Normalise case
    n = conn.execute(text(
        "UPDATE orders SET nuport_status = 'DELIVERED' WHERE nuport_status = 'delivered'"
    )).rowcount
    conn.commit()
    print(f"  ✓ Normalised 'delivered' → 'DELIVERED': {n} rows")

    # Show what will be nulled
    rows = conn.execute(text(f"""
        SELECT so_number, nuport_status FROM orders
        WHERE {GARBLED_STATUS_PATTERN}
        ORDER BY so_number
    """)).fetchall()
    print(f"\n  Garbled status rows to fix: {len(rows)}")
    for r in rows:
        print(f"    {r[0]}  →  '{r[1]}'")

    # Null them out
    n = conn.execute(text(f"""
        UPDATE orders SET nuport_status = NULL
        WHERE {GARBLED_STATUS_PATTERN}
    """)).rowcount
    conn.commit()
    print(f"\n  ✓ Nulled out {n} garbled status rows")
    return [r[0] for r in rows]


def fix_product_names(conn):
    # Restore from skus table where possible
    n = conn.execute(text(r"""
        UPDATE order_items oi
        SET product_name = s.product_name
        FROM skus s
        WHERE oi.sku = s.sku
          AND oi.product_name ~ '^\d+(\.\d+)?$'
          AND s.product_name IS NOT NULL
    """)).rowcount
    conn.commit()
    print(f"  ✓ Restored {n} garbled product names from skus table")

    # Null out any that couldn't be restored
    n = conn.execute(text(r"""
        UPDATE order_items
        SET product_name = NULL
        WHERE product_name ~ '^\d+(\.\d+)?$'
    """)).rowcount
    conn.commit()
    print(f"  ✓ Nulled {n} remaining unfixable product names")


def refetch_from_nuport(so_numbers: list):
    if not so_numbers:
        return
    print(f"\n  Re-fetching {len(so_numbers)} orders from Nuport API...")
    try:
        from apis.nuport import nuport
        from sync.nuport_sync import map_order, map_customer, UPSERT_ORDER, UPSERT_CUSTOMER

        ok = err = 0
        with get_connection() as conn:
            for so in so_numbers:
                try:
                    raw = nuport.get_order(so)
                    if not raw.get('internalId'):
                        print(f"    ⚠ {so}: not found in Nuport")
                        continue

                    cust = map_customer(raw)
                    customer_id = None
                    if cust:
                        r = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
                        customer_id = r[0] if r else None
                        conn.commit()

                    order = map_order(raw)
                    order['customer_id'] = customer_id
                    conn.execute(UPSERT_ORDER, order)
                    conn.commit()
                    print(f"    ✓ {so} → {order['nuport_status']}")
                    ok += 1
                except Exception as e:
                    print(f"    ✗ {so}: {e}")
                    err += 1

        print(f"\n  Re-fetched: {ok} ok, {err} errors")
    except Exception as e:
        print(f"  ⚠ Could not connect to Nuport API: {e}")
        print("    Orders left with NULL status — will be corrected on next sync")


def show_final_statuses(conn):
    print("\n=== Status counts after cleanup ===")
    rows = conn.execute(text(
        "SELECT nuport_status, COUNT(*) AS n FROM orders GROUP BY nuport_status ORDER BY n DESC"
    )).fetchall()
    for r in rows:
        print(f"  {r[1]:>8}  {r[0]}")


if __name__ == '__main__':
    print("=== Brain — Garbled Data Cleanup ===\n")

    with get_connection() as conn:
        print("── Step 1: Normalize COMPLETED → DELIVERED ──")
        normalize_completed(conn)

        print("\n── Step 2: Delete rows with garbled SO numbers ──")
        delete_garbled_so_numbers(conn)

        print("\n── Step 3: Fix garbled order statuses ──")
        garbled_so_numbers = fix_statuses(conn)

        print("\n── Step 4: Fix order_items product names ──")
        fix_product_names(conn)

        # Collect orders still at NULL status
        null_orders = conn.execute(text(
            "SELECT so_number FROM orders WHERE nuport_status IS NULL ORDER BY so_number"
        )).fetchall()
        null_so_numbers = [r[0] for r in null_orders if r[0] and not r[0].startswith(' ')]

        print("\n── Step 5: Final status summary ──")
        show_final_statuses(conn)

    # Combine: newly nulled + already-null
    to_refetch = list(dict.fromkeys(garbled_so_numbers + null_so_numbers))
    print(f"\n── Step 6: Re-fetch {len(to_refetch)} orders with NULL status from Nuport ──")
    refetch_from_nuport(to_refetch)

    print("\n✓ Done\n")
