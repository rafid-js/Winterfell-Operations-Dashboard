"""
Clean up duplicate order_items rows caused by multiple CSV imports.

Pattern: same (so_number, sku) stored twice — once with size=NULL and once
with a real size value, because the conflict key includes size and two CSV
exports had the attribute column filled differently.

Also handles range-SKU duplicates: e.g. sku='34384' (no size) alongside
sku='34384-34386' (with size), where the plain SKU is a prefix of the range SKU.

Safe to re-run — DELETE is idempotent.

Run:
  python cleanup_duplicate_items.py [--dry-run]
"""
import sys
sys.path.insert(0, '.')
from db import get_connection
from sqlalchemy import text

DRY_RUN = '--dry-run' in sys.argv

# ── Step 1: same SKU, null-size duplicate ─────────────────────────────────────
SQL_SAME_SKU = """
    DELETE FROM order_items oi1
    WHERE (oi1.size IS NULL OR oi1.size = '')
      AND EXISTS (
          SELECT 1 FROM order_items oi2
          WHERE oi2.so_number = oi1.so_number
            AND oi2.sku = oi1.sku
            AND oi2.size IS NOT NULL
            AND oi2.size <> ''
            AND oi2.color IS NOT DISTINCT FROM oi1.color
      )
"""

SQL_SAME_SKU_COUNT = """
    SELECT COUNT(*) FROM order_items oi1
    WHERE (oi1.size IS NULL OR oi1.size = '')
      AND EXISTS (
          SELECT 1 FROM order_items oi2
          WHERE oi2.so_number = oi1.so_number
            AND oi2.sku = oi1.sku
            AND oi2.size IS NOT NULL
            AND oi2.size <> ''
            AND oi2.color IS NOT DISTINCT FROM oi1.color
      )
"""

# ── Step 2: prefix-SKU duplicate (34384 vs 34384-34386) ──────────────────────
SQL_PREFIX_SKU = """
    DELETE FROM order_items oi1
    WHERE (oi1.size IS NULL OR oi1.size = '')
      AND EXISTS (
          SELECT 1 FROM order_items oi2
          WHERE oi2.so_number = oi1.so_number
            AND oi2.sku LIKE oi1.sku || '-%'
            AND oi2.size IS NOT NULL
            AND oi2.size <> ''
      )
"""

SQL_PREFIX_SKU_COUNT = """
    SELECT COUNT(*) FROM order_items oi1
    WHERE (oi1.size IS NULL OR oi1.size = '')
      AND EXISTS (
          SELECT 1 FROM order_items oi2
          WHERE oi2.so_number = oi1.so_number
            AND oi2.sku LIKE oi1.sku || '-%'
            AND oi2.size IS NOT NULL
            AND oi2.size <> ''
      )
"""


def main():
    print(f"\n=== order_items Duplicate Cleanup {'(DRY RUN) ' if DRY_RUN else ''}===\n")

    with get_connection() as conn:
        n1 = conn.execute(text(SQL_SAME_SKU_COUNT)).scalar()
        n2 = conn.execute(text(SQL_PREFIX_SKU_COUNT)).scalar()
        print(f"  Same-SKU null-size duplicates  : {n1}")
        print(f"  Prefix-SKU null-size duplicates: {n2}")
        print(f"  Total to remove                : {n1 + n2}\n")

        if DRY_RUN:
            print("  Dry run — no changes made.")
            return

        if n1 + n2 == 0:
            print("  Nothing to clean up.")
            return

        r1 = conn.execute(text(SQL_SAME_SKU)).rowcount
        conn.commit()
        print(f"  ✓ Deleted {r1} same-SKU null-size duplicates")

        r2 = conn.execute(text(SQL_PREFIX_SKU)).rowcount
        conn.commit()
        print(f"  ✓ Deleted {r2} prefix-SKU null-size duplicates")

        total = conn.execute(text("SELECT COUNT(*) FROM order_items")).scalar()
        print(f"\n  order_items rows remaining: {total:,}")

    print("\n✓ Done\n")


if __name__ == '__main__':
    main()
