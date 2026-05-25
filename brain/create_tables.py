import sys
from sqlalchemy import text

try:
    from db import get_connection
except RuntimeError as e:
    print(f"✗ Config error: {e}")
    sys.exit(1)

EXTENSIONS = [
    ("CREATE EXTENSION IF NOT EXISTS vector",   "pgvector"),
    ("CREATE EXTENSION IF NOT EXISTS pg_trgm",  "pg_trgm"),
]

TABLES = [
    ("customers", """
        CREATE TABLE IF NOT EXISTS customers (
            id                  SERIAL PRIMARY KEY,
            phone               VARCHAR(50) UNIQUE NOT NULL,
            email               VARCHAR(200),
            name                VARCHAR(200),
            address             TEXT,
            city                VARCHAR(100),
            district            VARCHAR(100),
            nuport_customer_id  VARCHAR(50),
            wc_customer_id      INTEGER,
            total_orders        INTEGER DEFAULT 0,
            total_spent         NUMERIC(12,2) DEFAULT 0,
            total_returned      INTEGER DEFAULT 0,
            avg_order_value     NUMERIC(12,2),
            first_order_date    TIMESTAMP,
            last_order_date     TIMESTAMP,
            customer_segment    VARCHAR(20),
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("orders", """
        CREATE TABLE IF NOT EXISTS orders (
            id                  SERIAL PRIMARY KEY,
            so_number           VARCHAR(20) UNIQUE NOT NULL,
            nuport_order_id     VARCHAR(50),
            wc_order_id         INTEGER,
            wc_order_number     VARCHAR(30),
            zoho_invoice_id     VARCHAR(50),
            pathao_waybill      VARCHAR(50),
            pathao_batch_id     VARCHAR(50),
            source_channel      VARCHAR(50),
            nuport_status       VARCHAR(50),
            wc_status           VARCHAR(30),
            payment_status      VARCHAR(30),
            customer_id         INTEGER REFERENCES customers(id),
            customer_name       VARCHAR(200),
            customer_phone      VARCHAR(50),
            product_total       NUMERIC(12,2),
            delivery_fee        NUMERIC(12,2),
            discount_amount     NUMERIC(12,2) DEFAULT 0,
            total_receivable    NUMERIC(12,2),
            collected_amount    NUMERIC(12,2),
            payout_amount       NUMERIC(12,2),
            order_date          TIMESTAMP,
            shipped_date        TIMESTAMP,
            delivered_date      TIMESTAMP,
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("financials", """
        CREATE TABLE IF NOT EXISTS financials (
            id                  SERIAL PRIMARY KEY,
            so_number           VARCHAR(20) REFERENCES orders(so_number),
            zoho_invoice_id     VARCHAR(50),
            pathao_batch_id     VARCHAR(50),
            pathao_waybill      VARCHAR(50),
            invoiced_amount     NUMERIC(12,2),
            collected_amount    NUMERIC(12,2),
            delivery_fee        NUMERIC(12,2),
            cod_fee             NUMERIC(12,2),
            payout_amount       NUMERIC(12,2),
            difference          NUMERIC(12,2),
            reconciled          BOOLEAN DEFAULT FALSE,
            reconciled_at       TIMESTAMP,
            reconcile_status    VARCHAR(30),
            zoho_marked_paid    BOOLEAN DEFAULT FALSE,
            zoho_paid_at        TIMESTAMP,
            created_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("pathao_waybills", """
        CREATE TABLE IF NOT EXISTS pathao_waybills (
            id                  SERIAL PRIMARY KEY,
            waybill_number      VARCHAR(50) UNIQUE NOT NULL,
            so_number           VARCHAR(20) REFERENCES orders(so_number),
            pathao_batch_id     VARCHAR(50),
            current_status      VARCHAR(50),
            previous_status     VARCHAR(50),
            status_history      JSONB,
            delivery_attempts   INTEGER DEFAULT 0,
            last_attempt_date   TIMESTAMP,
            last_location       VARCHAR(200),
            failure_reason      VARCHAR(200),
            collectable_amount  NUMERIC(12,2),
            collected_amount    NUMERIC(12,2),
            delivery_fee        NUMERIC(12,2),
            days_in_transit     INTEGER,
            is_lost             BOOLEAN DEFAULT FALSE,
            is_damaged          BOOLEAN DEFAULT FALSE,
            loss_value          NUMERIC(12,2),
            compensation_filed  BOOLEAN DEFAULT FALSE,
            compensation_amount NUMERIC(12,2),
            anomaly_flag        BOOLEAN DEFAULT FALSE,
            anomaly_reason      TEXT,
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("skus", """
        CREATE TABLE IF NOT EXISTS skus (
            id                  SERIAL PRIMARY KEY,
            sku                 VARCHAR(100) UNIQUE NOT NULL,
            product_name        VARCHAR(300),
            category            VARCHAR(100),
            color               VARCHAR(50),
            size                VARCHAR(20),
            gender              VARCHAR(20),
            cost_price          NUMERIC(10,2),
            selling_price       NUMERIC(10,2),
            current_stock       INTEGER DEFAULT 0,
            reorder_level       INTEGER DEFAULT 10,
            reorder_quantity    INTEGER DEFAULT 50,
            nuport_product_id   VARCHAR(50),
            image_url           TEXT,
            wc_product_id       INTEGER,
            wc_variation_id     INTEGER,
            zoho_item_id        VARCHAR(50),
            total_sold          INTEGER DEFAULT 0,
            total_returned      INTEGER DEFAULT 0,
            sell_through_rate   NUMERIC(5,2),
            is_active           BOOLEAN DEFAULT TRUE,
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("ad_spend", """
        CREATE TABLE IF NOT EXISTS ad_spend (
            id                  SERIAL PRIMARY KEY,
            date                DATE NOT NULL,
            account_id          VARCHAR(50),
            account_name        VARCHAR(100),
            campaign_id         VARCHAR(50),
            campaign_name       VARCHAR(200),
            adset_id            VARCHAR(50),
            adset_name          VARCHAR(200),
            spend_bdt           NUMERIC(12,2),
            impressions         INTEGER,
            clicks              INTEGER,
            ctr                 NUMERIC(6,4),
            cpm                 NUMERIC(10,2),
            cpc                 NUMERIC(10,2),
            orders_attributed   INTEGER,
            revenue_attributed  NUMERIC(12,2),
            true_revenue        NUMERIC(12,2),
            true_roas           NUMERIC(8,4),
            created_at          TIMESTAMP DEFAULT NOW()
        )
    """),
    ("knowledge_base", """
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id                  SERIAL PRIMARY KEY,
            source_type         VARCHAR(50),
            source_name         VARCHAR(200),
            source_date         TIMESTAMP,
            content             TEXT,
            summary             TEXT,
            tags                TEXT[],
            sentiment           VARCHAR(20),
            related_so          VARCHAR(20),
            related_sku         VARCHAR(100),
            related_supplier    VARCHAR(200),
            embedding           vector(1536),
            created_at          TIMESTAMP DEFAULT NOW()
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
    ("alerts_log", """
        CREATE TABLE IF NOT EXISTS alerts_log (
            id                  SERIAL PRIMARY KEY,
            alert_type          VARCHAR(50),
            severity            VARCHAR(20),
            title               VARCHAR(200),
            message             TEXT,
            related_so          VARCHAR(20),
            related_sku         VARCHAR(100),
            related_waybill     VARCHAR(50),
            data_snapshot       JSONB,
            resolved            BOOLEAN DEFAULT FALSE,
            resolved_at         TIMESTAMP,
            sent_whatsapp       BOOLEAN DEFAULT FALSE,
            sent_email          BOOLEAN DEFAULT FALSE,
            created_at          TIMESTAMP DEFAULT NOW()
        )
    """),
]

INDEXES = [
    ("idx_orders_so",             "CREATE INDEX IF NOT EXISTS idx_orders_so ON orders(so_number)"),
    ("idx_orders_wc_number",      "CREATE INDEX IF NOT EXISTS idx_orders_wc_number ON orders(wc_order_number)"),
    ("idx_orders_channel",        "CREATE INDEX IF NOT EXISTS idx_orders_channel ON orders(source_channel)"),
    ("idx_orders_status",         "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(nuport_status)"),
    ("idx_orders_date",           "CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date)"),
    ("idx_orders_customer",       "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)"),
    ("idx_customers_phone",       "CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone)"),
    ("idx_skus_sku",              "CREATE INDEX IF NOT EXISTS idx_skus_sku ON skus(sku)"),
    ("idx_skus_stock",            "CREATE INDEX IF NOT EXISTS idx_skus_stock ON skus(current_stock)"),
    ("idx_financials_so",         "CREATE INDEX IF NOT EXISTS idx_financials_so ON financials(so_number)"),
    ("idx_financials_reconciled", "CREATE INDEX IF NOT EXISTS idx_financials_reconciled ON financials(reconciled)"),
    ("idx_waybills_so",           "CREATE INDEX IF NOT EXISTS idx_waybills_so ON pathao_waybills(so_number)"),
    ("idx_waybills_status",       "CREATE INDEX IF NOT EXISTS idx_waybills_status ON pathao_waybills(current_status)"),
    ("idx_waybills_lost",         "CREATE INDEX IF NOT EXISTS idx_waybills_lost ON pathao_waybills(is_lost)"),
    ("idx_ad_spend_date",         "CREATE INDEX IF NOT EXISTS idx_ad_spend_date ON ad_spend(date)"),
    ("idx_knowledge_source",      "CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_base(source_type)"),
    ("idx_order_items_so",        "CREATE INDEX IF NOT EXISTS idx_order_items_so ON order_items(so_number)"),
    ("idx_order_items_sku",       "CREATE INDEX IF NOT EXISTS idx_order_items_sku ON order_items(sku)"),
    ("idx_order_items_unique",
     """CREATE UNIQUE INDEX IF NOT EXISTS idx_order_items_unique
        ON order_items(so_number, sku, COALESCE(size,''), COALESCE(color,''))"""),
]

VECTOR_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
    ON knowledge_base USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
"""

VIEWS = [
    ("daily_revenue", """
        CREATE OR REPLACE VIEW daily_revenue AS
        SELECT
            DATE(order_date)    AS date,
            COUNT(*)            AS total_orders,
            SUM(product_total)  AS gross_revenue,
            SUM(payout_amount)  AS collected_revenue,
            SUM(CASE WHEN nuport_status = 'Flagged_Returned' THEN 1 ELSE 0 END) AS returns,
            source_channel
        FROM orders
        GROUP BY DATE(order_date), source_channel
    """),
    ("inventory_health", """
        CREATE OR REPLACE VIEW inventory_health AS
        SELECT
            sku, product_name, category, size, color,
            current_stock, reorder_level,
            CASE
                WHEN current_stock = 0                        THEN 'OUT_OF_STOCK'
                WHEN current_stock <= reorder_level           THEN 'REORDER_NOW'
                WHEN current_stock <= reorder_level * 1.5     THEN 'RUNNING_LOW'
                ELSE 'OK'
            END AS stock_status,
            total_sold, sell_through_rate
        FROM skus
        WHERE is_active = TRUE
    """),
    ("pathao_loss_tracker", """
        CREATE OR REPLACE VIEW pathao_loss_tracker AS
        SELECT
            w.waybill_number, w.so_number, w.current_status,
            w.days_in_transit, w.is_lost, w.loss_value,
            w.compensation_filed, w.anomaly_flag, w.anomaly_reason,
            o.customer_name, o.product_total
        FROM pathao_waybills w
        LEFT JOIN orders o ON w.so_number = o.so_number
        WHERE w.anomaly_flag = TRUE OR w.is_lost = TRUE
    """),
    ("true_roas_by_campaign", """
        CREATE OR REPLACE VIEW true_roas_by_campaign AS
        SELECT
            a.date,
            a.campaign_name,
            a.account_name,
            a.spend_bdt,
            a.revenue_attributed        AS meta_claimed_revenue,
            COALESCE(SUM(o.payout_amount), 0) AS actual_collected,
            CASE WHEN a.spend_bdt > 0
                THEN COALESCE(SUM(o.payout_amount), 0) / a.spend_bdt
                ELSE 0
            END AS true_roas
        FROM ad_spend a
        LEFT JOIN orders o ON DATE(o.delivered_date) = a.date
        GROUP BY a.date, a.campaign_name, a.account_name, a.spend_bdt, a.revenue_attributed
    """),
]


def main():
    print("=== Winterfell Brain — Database Setup ===\n")
    pgvector_ok = True

    with get_connection() as conn:

        # ── Extensions ────────────────────────────────────────────
        print("Enabling extensions...")
        for sql, name in EXTENSIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ {name}")
            except Exception as e:
                print(f"  ✗ {name}: {e}")
                if "vector" in name:
                    pgvector_ok = False
                    print("    → knowledge_base table will be created without vector index")

        # ── Tables ────────────────────────────────────────────────
        print("\nCreating tables...")
        for name, sql in TABLES:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ Table {name} created")
            except Exception as e:
                print(f"  ✗ Table {name}: {e}")
                if name == "knowledge_base":
                    pgvector_ok = False

        # ── Indexes ───────────────────────────────────────────────
        print("\nCreating indexes...")
        ok = 0
        for idx_name, sql in INDEXES:
            try:
                conn.execute(text(sql))
                conn.commit()
                ok += 1
            except Exception as e:
                print(f"  ⚠ {idx_name}: {e}")
        print(f"  ✓ {ok}/{len(INDEXES)} standard indexes created")

        if pgvector_ok:
            try:
                conn.execute(text(VECTOR_INDEX))
                conn.commit()
                print("  ✓ Vector similarity index (ivfflat) created")
            except Exception as e:
                print(f"  ⚠ Vector index skipped: {e}")

        # ── Views ─────────────────────────────────────────────────
        print("\nCreating views...")
        for name, sql in VIEWS:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ View {name}")
            except Exception as e:
                print(f"  ✗ View {name}: {e}")

    print("\n✓ Database setup complete — run health_check.py to verify\n")


if __name__ == "__main__":
    main()
