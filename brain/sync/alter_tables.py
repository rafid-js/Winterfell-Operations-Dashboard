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

    # Widen phone columns — some numbers exceed 20 chars with country codes
    ("customers", "phone",
     "ALTER TABLE customers ALTER COLUMN phone TYPE VARCHAR(50)"),
    ("orders", "customer_phone",
     "ALTER TABLE orders ALTER COLUMN customer_phone TYPE VARCHAR(50)"),

    # Widen so_number — some Nuport IDs exceed 20 chars (FK tables first)
    ("order_items", "so_number",
     "ALTER TABLE order_items ALTER COLUMN so_number TYPE VARCHAR(50)"),
    ("financials", "so_number",
     "ALTER TABLE financials ALTER COLUMN so_number TYPE VARCHAR(50)"),
    ("pathao_waybills", "so_number",
     "ALTER TABLE pathao_waybills ALTER COLUMN so_number TYPE VARCHAR(50)"),
    ("orders", "so_number",
     "ALTER TABLE orders ALTER COLUMN so_number TYPE VARCHAR(50)"),

    # Widen wc_order_number to be safe
    ("orders", "wc_order_number",
     "ALTER TABLE orders ALTER COLUMN wc_order_number TYPE VARCHAR(50)"),
]

INDEXES = [
    ("idx_orders_wc_number",
     "CREATE INDEX IF NOT EXISTS idx_orders_wc_number ON orders(wc_order_number)"),
]

NEW_TABLES = [
    ("order_items", """
        CREATE TABLE IF NOT EXISTS order_items (
            id                   SERIAL PRIMARY KEY,
            so_number            VARCHAR(20) REFERENCES orders(so_number),
            sku                  VARCHAR(100),
            product_name         VARCHAR(300),
            size                 VARCHAR(50),
            color                VARCHAR(50),
            quantity             INTEGER DEFAULT 1,
            unit_price           NUMERIC(10,2),
            total_price          NUMERIC(10,2),
            item_discount        NUMERIC(10,2) DEFAULT 0,
            price_after_discount NUMERIC(10,2),
            created_at           TIMESTAMP DEFAULT NOW()
        )
    """),
]

NEW_INDEXES = [
    ("idx_order_items_so",
     "CREATE INDEX IF NOT EXISTS idx_order_items_so ON order_items(so_number)"),
    ("idx_order_items_sku",
     "CREATE INDEX IF NOT EXISTS idx_order_items_sku ON order_items(sku)"),
    ("idx_order_items_unique",
     """CREATE UNIQUE INDEX IF NOT EXISTS idx_order_items_unique
        ON order_items(so_number, sku, COALESCE(size,''), COALESCE(color,''))"""),
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

        print()
        for name, sql in NEW_TABLES:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ table {name}")

        for name, sql in NEW_INDEXES:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ index {name}")

    print("\n✓ Done\n")


if __name__ == '__main__':
    main()
