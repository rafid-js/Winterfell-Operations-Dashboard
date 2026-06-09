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
    # Return type — derived from SO number suffix (FPR, FPR-R, 1PR, etc.)
    ("orders", "return_type",
     "ALTER TABLE orders ADD COLUMN IF NOT EXISTS return_type VARCHAR(30)"),

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

    # Widen wc_order_number to be safe
    ("orders", "wc_order_number",
     "ALTER TABLE orders ALTER COLUMN wc_order_number TYPE VARCHAR(50)"),

    # Widen sku in tables not covered by views
    ("order_items",    "sku",         "ALTER TABLE order_items ALTER COLUMN sku TYPE VARCHAR(200)"),
    ("knowledge_base", "related_sku", "ALTER TABLE knowledge_base ALTER COLUMN related_sku TYPE VARCHAR(200)"),
    ("alerts_log",     "related_sku", "ALTER TABLE alerts_log ALTER COLUMN related_sku TYPE VARCHAR(200)"),

    # Widen customer city/district — WC puts full address in city field
    ("customers", "city",     "ALTER TABLE customers ALTER COLUMN city TYPE VARCHAR(500)"),
    ("customers", "district", "ALTER TABLE customers ALTER COLUMN district TYPE VARCHAR(100)"),
    ("customers", "name",     "ALTER TABLE customers ALTER COLUMN name TYPE VARCHAR(300)"),
    ("customers", "address",  "ALTER TABLE customers ALTER COLUMN address TYPE TEXT"),
]

# Columns blocked by views — drop views, widen, recreate
WIDEN_WITH_VIEWS = [
    # Drop all affected views first
    "DROP VIEW IF EXISTS pathao_loss_tracker",
    "DROP VIEW IF EXISTS inventory_health",

    # Widen so_number
    "ALTER TABLE pathao_waybills ALTER COLUMN so_number TYPE VARCHAR(50)",
    "ALTER TABLE orders ALTER COLUMN so_number TYPE VARCHAR(50)",

    # Widen sku in skus table
    "ALTER TABLE skus ALTER COLUMN sku TYPE VARCHAR(200)",

    # Recreate views
    """CREATE OR REPLACE VIEW pathao_loss_tracker AS
        SELECT w.waybill_number, w.so_number, w.current_status,
               w.days_in_transit, w.is_lost, w.loss_value,
               w.compensation_filed, w.anomaly_flag, w.anomaly_reason,
               o.customer_name, o.product_total
        FROM pathao_waybills w
        LEFT JOIN orders o ON w.so_number = o.so_number
        WHERE w.anomaly_flag = TRUE OR w.is_lost = TRUE""",
    """CREATE OR REPLACE VIEW inventory_health AS
        SELECT sku, product_name, category, size, color,
               current_stock, reorder_level,
               CASE
                   WHEN current_stock = 0                    THEN 'OUT_OF_STOCK'
                   WHEN current_stock <= reorder_level       THEN 'REORDER_NOW'
                   WHEN current_stock <= reorder_level * 1.5 THEN 'RUNNING_LOW'
                   ELSE 'OK'
               END AS stock_status,
               total_sold, sell_through_rate
        FROM skus WHERE is_active = TRUE""",
]

INDEXES = [
    ("idx_orders_wc_number",
     "CREATE INDEX IF NOT EXISTS idx_orders_wc_number ON orders(wc_order_number)"),
]

NEW_TABLES = [
    ("return_suffix_types", """
        CREATE TABLE IF NOT EXISTS return_suffix_types (
            suffix      VARCHAR(20) PRIMARY KEY,
            label       VARCHAR(100) NOT NULL,
            description TEXT
        )
    """),
    ("sync_log", """
        CREATE TABLE IF NOT EXISTS sync_log (
            id              SERIAL PRIMARY KEY,
            source          VARCHAR(30) NOT NULL,
            sync_type       VARCHAR(30) NOT NULL,
            started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMP,
            last_record_at  TIMESTAMP,
            records_synced  INTEGER DEFAULT 0,
            status          VARCHAR(10) DEFAULT 'running',
            error_msg       TEXT
        )
    """),
    ("system_status", """
        CREATE TABLE IF NOT EXISTS system_status (
            id                    SERIAL PRIMARY KEY,
            script_name           VARCHAR(100) UNIQUE NOT NULL,
            display_name          VARCHAR(200),
            schedule              VARCHAR(100),
            last_run_at           TIMESTAMP,
            last_run_status       VARCHAR(20) DEFAULT 'NEVER',
            last_run_duration_sec INTEGER,
            last_error            TEXT,
            rows_processed        INTEGER,
            next_run_at           TIMESTAMP,
            is_enabled            BOOLEAN DEFAULT TRUE,
            run_count             INTEGER DEFAULT 0,
            fail_count            INTEGER DEFAULT 0
        )
    """),
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
    ("idx_ad_spend_unique",       "CREATE UNIQUE INDEX IF NOT EXISTS idx_ad_spend_unique ON ad_spend(date, campaign_id, adset_id)"),
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
        for sql in WIDEN_WITH_VIEWS:
            conn.execute(text(sql))
            conn.commit()
        print("  ✓ so_number/sku widened, views recreated")

        for name, sql in NEW_TABLES:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ table {name}")

        for name, sql in NEW_INDEXES:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ index {name}")

        # Seed system_status with all known scripts
        conn.execute(text("""
            INSERT INTO system_status (script_name, display_name, schedule) VALUES
              ('nuport_sync',    'Nuport → Brain sync',          'Every 15 min'),
              ('wc_sync',        'WooCommerce → SKU images',     'Every 6 hours'),
              ('zoho_sync',      'Zoho Books → Brain sync',      'Every 6 hours'),
              ('reorder_engine', 'Inventory reorder check',      'Every 6 hours'),
              ('meta_sync',      'Meta Ads → Brain sync',        'Every 6 hours'),
              ('pathao_sync',    'Pathao waybill sync',          'Daily 8AM'),
              ('reconciliation', 'Pathao × Zoho reconciliation', 'Daily 8AM'),
              ('daily_briefing', 'Daily Telegram briefing',      'Daily 9AM')
            ON CONFLICT (script_name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                schedule     = EXCLUDED.schedule
        """))
        conn.commit()
        print("  ✓ system_status seeded")

        # Remove WC-XXXX orphan orders (WC order sync abandoned — Nuport is source of truth)
        items_deleted = conn.execute(text(
            "DELETE FROM order_items WHERE so_number LIKE 'WC-%'"
        )).rowcount
        orders_deleted = conn.execute(text(
            "DELETE FROM orders WHERE so_number LIKE 'WC-%'"
        )).rowcount
        conn.commit()
        print(f"  ✓ cleaned up {orders_deleted} WC-XXXX orders, {items_deleted} items")

        # Seed return_suffix_types reference table
        conn.execute(text("""
            INSERT INTO return_suffix_types (suffix, label, description) VALUES
                ('FPR',   'Pending Return',   'Product is still with courier (Pathao), not yet received at our warehouse'),
                ('FPR-R', 'Returned',         'Product received back at our warehouse — courier completed the return'),
                ('1PR',   'Partial Return 1', 'First partial return — some items returned, remaining delivered'),
                ('2PR',   'Partial Return 2', 'Second partial return on the same order'),
                ('3PR',   'Partial Return 3', 'Third partial return on the same order'),
                ('1PR-R', 'Partial Returned 1', 'First partial return received back at warehouse'),
                ('2PR-R', 'Partial Returned 2', 'Second partial return received back at warehouse'),
                ('PR',    'Partial Return',   'General partial return')
            ON CONFLICT (suffix) DO UPDATE SET
                label       = EXCLUDED.label,
                description = EXCLUDED.description
        """))
        conn.commit()
        print("  ✓ return_suffix_types seeded (8 suffix types)")

        # Backfill return_type on existing orders from SO number suffix
        backfill = conn.execute(text("""
            UPDATE orders SET return_type =
                CASE
                    WHEN so_number ~* '-FPR-R$'           THEN 'returned'
                    WHEN so_number ~* '-[0-9]+PR-R$'      THEN 'returned'
                    WHEN so_number ~* '-FPR$'             THEN 'pending_return'
                    WHEN so_number ~* '-[0-9]+PR$'        THEN 'partial_return'
                END
            WHERE so_number ~* '-(FPR(-R)?|[0-9]+PR(-R)?)$'
              AND return_type IS NULL
        """)).rowcount
        conn.commit()
        print(f"  ✓ backfilled return_type on {backfill} existing return orders")

    print("\n✓ Done\n")


if __name__ == '__main__':
    main()
