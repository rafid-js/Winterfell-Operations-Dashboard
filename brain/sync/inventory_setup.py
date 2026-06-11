"""
Inventory module — schema migration.

Creates the reorder_queue, dead_stock_log, true_demand_log and size_profiles
tables, their indexes, the test-batch columns on skus, and seeds the
inventory cron rows into system_status.

Safe to re-run — uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / ON CONFLICT.

Run once from brain/:
  python -m sync.inventory_setup
"""
import os
import sys

from sqlalchemy import text

# brain/ is the parent of this file's sync/ directory — add it so `db` imports
# work regardless of the current working directory or platform.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection  # noqa: E402


REORDER_QUEUE_SQL = """
CREATE TABLE IF NOT EXISTS reorder_queue (
    id                        SERIAL PRIMARY KEY,
    sku_base                  VARCHAR(200) NOT NULL UNIQUE,
    product_name              VARCHAR(300),
    category                  VARCHAR(100),
    urgency                   VARCHAR(20) NOT NULL,
    recommended_total         INTEGER NOT NULL DEFAULT 0,
    size_breakdown            JSONB NOT NULL,
    net_need_breakdown        JSONB,
    auto_qty_breakdown        JSONB,
    waiting_orders_breakdown  JSONB,
    current_stock_breakdown   JSONB,
    sales_30d_breakdown       JSONB,
    days_until_stockout       INTEGER,
    total_waiting_orders      INTEGER DEFAULT 0,
    capital_at_risk_bdt       NUMERIC(12,2),
    kill_chain_score          NUMERIC(4,2) DEFAULT 0,
    kill_chain_stage          VARCHAR(20),
    sell_through_pct          NUMERIC(5,2),
    days_since_last_sale      INTEGER,
    stock_age_days            INTEGER,
    po_created                BOOLEAN DEFAULT FALSE,
    po_id                     VARCHAR(20),
    suppressed                BOOLEAN DEFAULT FALSE,
    calculated_at             TIMESTAMP DEFAULT NOW(),
    expires_at                TIMESTAMP
);
"""

DEAD_STOCK_LOG_SQL = """
CREATE TABLE IF NOT EXISTS dead_stock_log (
    id                      SERIAL PRIMARY KEY,
    sku_base                VARCHAR(200) NOT NULL,
    product_name            VARCHAR(300),
    size                    VARCHAR(20),
    units_stuck             INTEGER,
    capital_locked_bdt      NUMERIC(12,2),
    kill_chain_stage        VARCHAR(20),
    kill_chain_score        NUMERIC(4,2),
    days_since_last_sale    INTEGER,
    sell_through_pct        NUMERIC(5,2),
    suggested_action        VARCHAR(50),
    suggested_discount_pct  INTEGER,
    bundle_with_sku         VARCHAR(200),
    claude_recommendation   TEXT,
    brand_risk_rating       VARCHAR(10),
    status                  VARCHAR(20) DEFAULT 'Active',
    strike_count            INTEGER DEFAULT 0,
    logged_at               TIMESTAMP DEFAULT NOW(),
    resolved_at             TIMESTAMP
);
"""

TRUE_DEMAND_LOG_SQL = """
CREATE TABLE IF NOT EXISTS true_demand_log (
    id                SERIAL PRIMARY KEY,
    sku_base          VARCHAR(200) NOT NULL,
    size              VARCHAR(20),
    period_start      DATE NOT NULL,
    period_end        DATE NOT NULL,
    orders_placed     INTEGER,
    orders_delivered  INTEGER,
    orders_cancelled  INTEGER,
    orders_returned   INTEGER,
    true_demand       INTEGER,
    ghost_revenue_bdt NUMERIC(12,2),
    stockout_days     INTEGER,
    lost_sales_bdt    NUMERIC(12,2),
    calculated_at     TIMESTAMP DEFAULT NOW()
);
"""

SIZE_PROFILES_SQL = """
CREATE TABLE IF NOT EXISTS size_profiles (
    id                SERIAL PRIMARY KEY,
    category          VARCHAR(100),
    size              VARCHAR(20),
    distribution_pct  NUMERIC(5,2),
    sample_size       INTEGER,
    calculated_at     TIMESTAMP DEFAULT NOW()
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_rq_urgency    ON reorder_queue(urgency);",
    "CREATE INDEX IF NOT EXISTS idx_rq_sku        ON reorder_queue(sku_base);",
    "CREATE INDEX IF NOT EXISTS idx_rq_kill       ON reorder_queue(kill_chain_stage);",
    "CREATE INDEX IF NOT EXISTS idx_rq_suppressed ON reorder_queue(suppressed);",
    "CREATE INDEX IF NOT EXISTS idx_ds_sku        ON dead_stock_log(sku_base);",
    "CREATE INDEX IF NOT EXISTS idx_ds_status     ON dead_stock_log(status);",
    "CREATE INDEX IF NOT EXISTS idx_td_sku        ON true_demand_log(sku_base);",
    "CREATE INDEX IF NOT EXISTS idx_td_period     ON true_demand_log(period_start);",
    "CREATE INDEX IF NOT EXISTS idx_sp_cat        ON size_profiles(category);",
]

# Test-batch tracking columns on the existing skus table (Part 8).
SKUS_COLUMNS_SQL = [
    "ALTER TABLE skus ADD COLUMN IF NOT EXISTS batch_type VARCHAR(20) DEFAULT 'Standard';",
    "ALTER TABLE skus ADD COLUMN IF NOT EXISTS test_batch_date DATE;",
    "ALTER TABLE skus ADD COLUMN IF NOT EXISTS test_batch_qty INTEGER;",
    "ALTER TABLE skus ADD COLUMN IF NOT EXISTS test_day7_sellthrough NUMERIC(5,2);",
    "ALTER TABLE skus ADD COLUMN IF NOT EXISTS test_verdict VARCHAR(20);",
]

SYSTEM_STATUS_SQL = """
INSERT INTO system_status (script_name, display_name, schedule) VALUES
    ('reorder_engine',    'Reorder engine + Kill chain', 'Every 6 hrs'),
    ('true_demand',       'True demand calculator',      'Daily 7AM'),
    ('size_intelligence', 'Size intelligence learner',   'Weekly Sun 6AM'),
    ('test_batch',        'Test batch evaluator',        'Daily 8AM')
ON CONFLICT (script_name) DO NOTHING;
"""

# Inventory ↔ Supply Chain two-way sync (safe to re-run).
PO_SYNC_SQL = [
    # FIX 6: PO status display column on reorder_queue.
    "ALTER TABLE reorder_queue ADD COLUMN IF NOT EXISTS po_status_display VARCHAR(30);",

    # Immediate fix: clear any orphan PO references before adding the FK.
    """UPDATE reorder_queue
       SET po_created = FALSE, po_id = NULL
       WHERE po_id IS NOT NULL
         AND po_id NOT IN (SELECT po_id FROM purchase_orders);""",

    # FIX 1b: FK with ON DELETE SET NULL so deleting a PO auto-clears the link.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_rq_po'
          AND table_name      = 'reorder_queue'
      ) THEN
        ALTER TABLE reorder_queue
          ADD CONSTRAINT fk_rq_po
          FOREIGN KEY (po_id)
          REFERENCES purchase_orders(po_id)
          ON DELETE SET NULL;
      END IF;
    END $$;""",

    # FIX 1c+d: Trigger that resets po_created whenever po_id is cleared.
    """CREATE OR REPLACE FUNCTION reset_po_created()
    RETURNS TRIGGER AS $$
    BEGIN
      IF NEW.po_id IS NULL THEN
        NEW.po_created        := FALSE;
        NEW.po_status_display := NULL;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;""",

    "DROP TRIGGER IF EXISTS trg_reset_po_created ON reorder_queue;",

    """CREATE TRIGGER trg_reset_po_created
       BEFORE UPDATE ON reorder_queue
       FOR EACH ROW
       EXECUTE FUNCTION reset_po_created();""",
]


def run():
    with get_connection() as conn:
        print("Creating reorder_queue ...")
        conn.execute(text(REORDER_QUEUE_SQL))
        print("Creating dead_stock_log ...")
        conn.execute(text(DEAD_STOCK_LOG_SQL))
        print("Creating true_demand_log ...")
        conn.execute(text(TRUE_DEMAND_LOG_SQL))
        print("Creating size_profiles ...")
        conn.execute(text(SIZE_PROFILES_SQL))

        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            conn.execute(text(stmt))

        print("Adding test-batch columns to skus ...")
        for stmt in SKUS_COLUMNS_SQL:
            conn.execute(text(stmt))

        print("Seeding system_status (inventory crons) ...")
        conn.execute(text(SYSTEM_STATUS_SQL))

        print("Adding PO sync FK + trigger ...")
        for stmt in PO_SYNC_SQL:
            conn.execute(text(stmt))

        conn.commit()
    print("Inventory migration complete.")


if __name__ == '__main__':
    run()
