"""
Supply Chain module — Phase 1 migration.

Creates suppliers, purchase_orders and po_timeline tables (in FK-dependency
order), supporting indexes, the active_po_summary view, and seeds the
po_risk_engine row into system_status.

Safe to re-run — uses IF NOT EXISTS / CREATE OR REPLACE / ON CONFLICT throughout.

Run once from brain/:
  python -m sync.supply_chain_setup
"""
import os
import sys
from sqlalchemy import text

# brain/ is the parent of this file's sync/ directory — add it so `db` imports
# work regardless of the current working directory or platform.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection


SUPPLIERS_SQL = """
CREATE TABLE IF NOT EXISTS suppliers (
    id                    SERIAL PRIMARY KEY,
    name                  VARCHAR(200) UNIQUE NOT NULL,
    phone                 VARCHAR(20),
    whatsapp              VARCHAR(20),
    email                 VARCHAR(200),
    speciality            TEXT[],
    location              VARCHAR(200),
    reliability_score     NUMERIC(4,2) DEFAULT 5.00,
    total_pos             INTEGER DEFAULT 0,
    completed_pos         INTEGER DEFAULT 0,
    on_time_count         INTEGER DEFAULT 0,
    delayed_count         INTEGER DEFAULT 0,
    avg_lead_days         NUMERIC(5,1),
    quality_issue_count   INTEGER DEFAULT 0,
    last_po_date          DATE,
    is_preferred          BOOLEAN DEFAULT FALSE,
    is_blacklisted        BOOLEAN DEFAULT FALSE,
    notes                 TEXT,
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW()
);
"""

PURCHASE_ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS purchase_orders (
    id                    SERIAL PRIMARY KEY,
    po_id                 VARCHAR(20) UNIQUE NOT NULL,
    sku                   VARCHAR(200) REFERENCES skus(sku),
    product_name          VARCHAR(300),
    supplier_id           INTEGER REFERENCES suppliers(id),
    quantity_ordered      INTEGER NOT NULL,
    quantity_received     INTEGER DEFAULT 0,
    quantity_rejected     INTEGER DEFAULT 0,
    unit_cost_bdt         NUMERIC(10,2),
    total_cost_bdt        NUMERIC(12,2),
    advance_paid_bdt      NUMERIC(12,2) DEFAULT 0,
    balance_due_bdt       NUMERIC(12,2),
    total_paid_bdt        NUMERIC(12,2) DEFAULT 0,
    current_stage         VARCHAR(50) DEFAULT 'PO Issued',
    stage_completion_pct  INTEGER DEFAULT 0,
    po_status             VARCHAR(20) DEFAULT 'Active',
    issued_date           DATE NOT NULL,
    due_date              DATE NOT NULL,
    expected_delivery     DATE,
    actual_delivery       DATE,
    triggered_by          VARCHAR(50) DEFAULT 'manual',
    linked_so_numbers     TEXT[],
    meta_campaign_paused  BOOLEAN DEFAULT FALSE,
    meta_campaign_id      VARCHAR(100),
    notes                 TEXT,
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW()
);
"""

PO_TIMELINE_SQL = """
CREATE TABLE IF NOT EXISTS po_timeline (
    id              SERIAL PRIMARY KEY,
    event_id        VARCHAR(20) UNIQUE,
    po_id           VARCHAR(20) REFERENCES purchase_orders(po_id),
    stage           VARCHAR(50) NOT NULL,
    event_title     VARCHAR(200) NOT NULL,
    event_note      TEXT,
    amount_bdt      NUMERIC(12,2),
    source_type     VARCHAR(20) NOT NULL,
    source_ref      VARCHAR(200),
    logged_by       VARCHAR(100),
    event_date      TIMESTAMP NOT NULL DEFAULT NOW(),
    is_alert        BOOLEAN DEFAULT FALSE,
    alert_severity  VARCHAR(20),
    created_at      TIMESTAMP DEFAULT NOW()
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_po_status   ON purchase_orders(po_status);",
    "CREATE INDEX IF NOT EXISTS idx_po_stage    ON purchase_orders(current_stage);",
    "CREATE INDEX IF NOT EXISTS idx_po_due      ON purchase_orders(due_date);",
    "CREATE INDEX IF NOT EXISTS idx_po_supplier ON purchase_orders(supplier_id);",
    "CREATE INDEX IF NOT EXISTS idx_tl_po       ON po_timeline(po_id);",
    "CREATE INDEX IF NOT EXISTS idx_tl_source   ON po_timeline(source_type);",
    "CREATE INDEX IF NOT EXISTS idx_tl_date     ON po_timeline(event_date);",
]

VIEW_SQL = """
CREATE OR REPLACE VIEW active_po_summary AS
SELECT
    po.po_id, po.product_name, po.sku,
    s.name AS supplier_name,
    po.quantity_ordered, po.quantity_received,
    po.current_stage, po.stage_completion_pct, po.po_status,
    po.issued_date, po.due_date,
    po.unit_cost_bdt, po.total_cost_bdt,
    po.advance_paid_bdt, po.balance_due_bdt, po.total_paid_bdt,
    po.notes,
    CASE WHEN po.actual_delivery IS NULL
         THEN GREATEST((CURRENT_DATE - po.due_date)::INTEGER, 0)
         ELSE 0 END AS days_overdue,
    s.reliability_score AS supplier_score,
    (SELECT COUNT(*) FROM po_timeline t WHERE t.po_id = po.po_id) AS event_count,
    (SELECT MAX(event_date) FROM po_timeline t WHERE t.po_id = po.po_id) AS last_update
FROM purchase_orders po
LEFT JOIN suppliers s ON po.supplier_id = s.id
WHERE po.po_status NOT IN ('Completed', 'Cancelled')
ORDER BY
    CASE po.po_status WHEN 'Delayed' THEN 1 WHEN 'At Risk' THEN 2 ELSE 3 END,
    po.due_date ASC;
"""

SYSTEM_STATUS_SQL = """
INSERT INTO system_status (script_name, display_name, schedule)
VALUES ('po_risk_engine', 'PO risk recalculation', 'Every 6 hours')
ON CONFLICT (script_name) DO NOTHING;
"""

# Phase 2 incremental migrations — safe to re-run.
MIGRATIONS_SQL = [
    "ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS size_breakdown JSONB;",
    "ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS po_products JSONB;",

    # Inventory ↔ SC sync column (also in inventory_setup; ADD IF NOT EXISTS is idempotent).
    "ALTER TABLE reorder_queue ADD COLUMN IF NOT EXISTS po_status_display VARCHAR(30);",

    # FK on po_timeline: cascade deletes so removing a PO removes its events.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_tl_po' AND table_name = 'po_timeline'
      ) THEN
        -- Drop the plain-reference constraint that ships with the table definition.
        ALTER TABLE po_timeline DROP CONSTRAINT IF EXISTS po_timeline_po_id_fkey;
        ALTER TABLE po_timeline
          ADD CONSTRAINT fk_tl_po
            FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id) ON DELETE CASCADE;
      END IF;
    END $$;""",

    # FK on reorder_queue: set NULL so deleting a PO clears the link.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_rq_po' AND table_name = 'reorder_queue'
      ) THEN
        UPDATE reorder_queue
          SET po_created = FALSE, po_id = NULL
          WHERE po_id IS NOT NULL
            AND po_id NOT IN (SELECT po_id FROM purchase_orders);
        ALTER TABLE reorder_queue
          ADD CONSTRAINT fk_rq_po
            FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id) ON DELETE SET NULL;
      END IF;
    END $$;""",

    # Trigger: auto-clear po_created + po_status_display when po_id is NULLed.
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

    # ── one-time data repair: backfill reorder_queue rows that were created
    # before the SC→Inventory sync was implemented. Finds the most recent
    # active/delayed/at-risk PO whose product name (after stripping the size
    # suffix) matches the sku_base, and links the row if it is currently
    # unlinked.  Only rows with po_created=FALSE or po_id IS NULL are touched.
    """WITH best_po AS (
        SELECT DISTINCT ON (rq.sku_base)
            rq.sku_base,
            po.po_id,
            po.po_status
        FROM reorder_queue rq
        JOIN purchase_orders po ON
            TRIM(regexp_replace(
                SPLIT_PART(po.product_name, ' + ', 1),
                '\\s*-\\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[2-5][0-9])(\\s*\\([^)]*\\))?\\s*$',
                '',
                'i'
            )) = rq.sku_base
        WHERE (rq.po_created = FALSE OR rq.po_id IS NULL)
          AND po.po_status NOT IN ('Completed', 'Cancelled')
        ORDER BY rq.sku_base, po.issued_date DESC
    )
    UPDATE reorder_queue rq
    SET
        po_created        = TRUE,
        po_id             = best_po.po_id,
        po_status_display = best_po.po_status
    FROM best_po
    WHERE rq.sku_base = best_po.sku_base
      AND (rq.po_created = FALSE OR rq.po_id IS NULL);""",

    # Clean up any stale po_id references that point to deleted POs (safety
    # net in case the FK SET NULL didn't fire for pre-migration rows).
    """UPDATE reorder_queue
    SET po_created = FALSE, po_id = NULL, po_status_display = NULL
    WHERE po_id IS NOT NULL
      AND po_id NOT IN (SELECT po_id FROM purchase_orders);""",
]


def run():
    with get_connection() as conn:
        print("Creating suppliers ...")
        conn.execute(text(SUPPLIERS_SQL))

        print("Creating purchase_orders ...")
        conn.execute(text(PURCHASE_ORDERS_SQL))

        print("Creating po_timeline ...")
        conn.execute(text(PO_TIMELINE_SQL))

        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            conn.execute(text(stmt))

        print("Creating active_po_summary view ...")
        conn.execute(text(VIEW_SQL))

        print("Seeding system_status (po_risk_engine) ...")
        conn.execute(text(SYSTEM_STATUS_SQL))

        print("Applying incremental migrations ...")
        for stmt in MIGRATIONS_SQL:
            conn.execute(text(stmt))

        conn.commit()
    print("Supply Chain migration complete.")


if __name__ == '__main__':
    run()
