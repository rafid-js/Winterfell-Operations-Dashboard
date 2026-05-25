"""
Adds columns that were identified after the initial Brain setup.
Safe to re-run — uses IF NOT EXISTS / IF EXISTS throughout.

Run once:
  python -m sync.alter_tables
"""
import sys
from sqlalchemy import text
sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import get_connection

MIGRATIONS = [
    # Universal WooCommerce order number (WIN-XXXXX format)
    ("orders",  "wc_order_number",
     "ALTER TABLE orders ADD COLUMN IF NOT EXISTS wc_order_number VARCHAR(30)"),

    # Product image URL (pulled from WooCommerce)
    ("skus",    "image_url",
     "ALTER TABLE skus ADD COLUMN IF NOT EXISTS image_url TEXT"),
]

INDEXES = [
    ("idx_orders_wc_number",
     "CREATE INDEX IF NOT EXISTS idx_orders_wc_number ON orders(wc_order_number)"),
]


def main():
    print("=== Brain — Alter Tables ===\n")
    with get_connection() as conn:
        for table, col, sql in MIGRATIONS:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ {table}.{col}")

        for name, sql in INDEXES:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ index {name}")

    print("\n✓ Done\n")


if __name__ == '__main__':
    main()
