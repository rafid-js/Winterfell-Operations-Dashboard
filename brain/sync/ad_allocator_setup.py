"""
Ad Allocator ("Warchest") — schema migration.

Growth/Ads module of Winterfell OS. Ranks product groups by profit, compares
actual ad-spend share to a profit-based recommended share, and proposes a
next-7-day budget split that reaches Meta only after human approval.

Design note: the allocator reads spend/orders/stock LIVE from the brain tables
that already sync (ad_spend, order_items + orders, skus) rather than keeping its
own copies (the spec's meta_daily / order_line / inventory_snapshot). This keeps
one source of truth. Only the allocator-specific tables are created here, all
namespaced aa_* so they are easy to find and tear down:

  aa_product_group  — a product group (ads target category groups, not SKUs)
  aa_ad_product_map — Meta ad/adset/campaign -> product group, with split weight
  aa_status_map     — WooCommerce custom status slug -> counted bucket
  aa_config         — editable constants (COGS, kept rate, budgets, guardrails)
  aa_allocation_run — one allocation run (period, budget, params snapshot)
  aa_allocation_row — per-group result of a run (shares, amount, action, reason)
  aa_change_request — a proposed Meta budget change, pending human approval

Safe to re-run (IF NOT EXISTS / ON CONFLICT DO NOTHING).

Run once from brain/:
  python -m sync.ad_allocator_setup
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection  # noqa: E402


PRODUCT_GROUP_SQL = """
CREATE TABLE IF NOT EXISTS aa_product_group (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200) NOT NULL UNIQUE,
    category      VARCHAR(100),
    is_new        BOOLEAN DEFAULT FALSE,
    is_clearance  BOOLEAN DEFAULT FALSE,
    cogs_pct      NUMERIC(5,4),          -- overrides the global COGS when set
    aov_override  NUMERIC(10,2),
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);
"""

# One row per (ad OR adset OR campaign) -> product group. weight splits spend
# when a single ad covers several groups (default 1.0 = all to one group).
AD_PRODUCT_MAP_SQL = """
CREATE TABLE IF NOT EXISTS aa_ad_product_map (
    id               SERIAL PRIMARY KEY,
    meta_ad_id       VARCHAR(50),
    meta_adset_id    VARCHAR(50),
    meta_campaign_id VARCHAR(50),
    product_group_id INTEGER REFERENCES aa_product_group(id) ON DELETE CASCADE,
    weight           NUMERIC(5,4) DEFAULT 1.0,
    created_at       TIMESTAMP DEFAULT NOW()
);
"""

STATUS_MAP_SQL = """
CREATE TABLE IF NOT EXISTS aa_status_map (
    status_slug  VARCHAR(80) PRIMARY KEY,
    bucket       VARCHAR(20) NOT NULL,   -- counted | excluded | returned
    updated_at   TIMESTAMP DEFAULT NOW()
);
"""

# Explicit SKU -> product group rollup (replaces name-substring matching, so a
# SKU belongs to exactly one group and category rows never mix with SKU rows).
SKU_GROUP_MAP_SQL = """
CREATE TABLE IF NOT EXISTS aa_sku_group_map (
    sku              VARCHAR(200) PRIMARY KEY,
    product_group_id INTEGER REFERENCES aa_product_group(id) ON DELETE CASCADE,
    updated_at       TIMESTAMP DEFAULT NOW()
);
"""

# Per-AD daily spend + purchases pulled from Meta at level=ad, so spend can be
# attributed to groups through aa_ad_product_map (meta_ad_id).
META_AD_DAILY_SQL = """
CREATE TABLE IF NOT EXISTS aa_meta_ad_daily (
    date           DATE NOT NULL,
    account_id     VARCHAR(50),
    ad_id          VARCHAR(50) NOT NULL,
    ad_name        VARCHAR(300),
    adset_id       VARCHAR(50),
    adset_name     VARCHAR(300),
    campaign_id    VARCHAR(50),
    campaign_name  VARCHAR(300),
    spend_bdt      NUMERIC(14,2),
    purchases      INTEGER,
    purchase_value NUMERIC(14,2),
    updated_at     TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (date, ad_id)
);
"""

# Per-catalog-product daily spend from Meta's product_id breakdown. retailer_id
# is the WooCommerce variation ID, joined to skus.wc_variation_id -> group. This
# is the primary spend attribution for catalog / Advantage+ campaigns.
PRODUCT_SPEND_DAILY_SQL = """
CREATE TABLE IF NOT EXISTS aa_product_spend_daily (
    date           DATE NOT NULL,
    account_id     VARCHAR(50),
    retailer_id    VARCHAR(80) NOT NULL,
    product_name   VARCHAR(300),
    spend_bdt      NUMERIC(14,2),
    purchases      INTEGER,
    purchase_value NUMERIC(14,2),
    updated_at     TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (date, retailer_id)
);
"""

CONFIG_SQL = """
CREATE TABLE IF NOT EXISTS aa_config (
    key         VARCHAR(60) PRIMARY KEY,
    value       NUMERIC(14,4) NOT NULL,
    note        TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);
"""

ALLOCATION_RUN_SQL = """
CREATE TABLE IF NOT EXISTS aa_allocation_run (
    id            SERIAL PRIMARY KEY,
    run_at        TIMESTAMP DEFAULT NOW(),
    period        VARCHAR(10),            -- 7d / 15d / 30d / 6m / 1y
    total_budget  NUMERIC(14,2),
    params_json   JSONB DEFAULT '{}',
    created_by    VARCHAR(100)
);
"""

ALLOCATION_ROW_SQL = """
CREATE TABLE IF NOT EXISTS aa_allocation_row (
    id               SERIAL PRIMARY KEY,
    run_id           INTEGER REFERENCES aa_allocation_run(id) ON DELETE CASCADE,
    product_group_id INTEGER REFERENCES aa_product_group(id),
    rank             INTEGER,
    actual_share     NUMERIC(7,4),
    rec_share        NUMERIC(7,4),
    rec_amount       NUMERIC(14,2),
    gap              NUMERIC(14,2),
    action           VARCHAR(30),
    reason_json      JSONB DEFAULT '{}',
    metrics_json     JSONB DEFAULT '{}'
);
"""

# status: pending -> approved|rejected ; approved -> pushed|failed
CHANGE_REQUEST_SQL = """
CREATE TABLE IF NOT EXISTS aa_change_request (
    id               SERIAL PRIMARY KEY,
    run_id           INTEGER REFERENCES aa_allocation_run(id) ON DELETE SET NULL,
    product_group_id INTEGER REFERENCES aa_product_group(id),
    ad_set_id        VARCHAR(50),
    from_amount      NUMERIC(14,2),
    to_amount        NUMERIC(14,2),
    status           VARCHAR(20) DEFAULT 'pending',
    approved_by      VARCHAR(100),
    approved_at      TIMESTAMP,
    pushed_at        TIMESTAMP,
    meta_response    JSONB,
    created_at       TIMESTAMP DEFAULT NOW()
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_aa_map_ad ON aa_ad_product_map(meta_ad_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_map_adset ON aa_ad_product_map(meta_adset_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_map_campaign ON aa_ad_product_map(meta_campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_map_group ON aa_ad_product_map(product_group_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_skumap_group ON aa_sku_group_map(product_group_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_adday_ad ON aa_meta_ad_daily(ad_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_adday_date ON aa_meta_ad_daily(date);",
    "CREATE INDEX IF NOT EXISTS idx_aa_adday_adset ON aa_meta_ad_daily(adset_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_prodspend_rid ON aa_product_spend_daily(retailer_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_prodspend_date ON aa_product_spend_daily(date);",
    "CREATE INDEX IF NOT EXISTS idx_aa_run_row ON aa_allocation_row(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_aa_cr_status ON aa_change_request(status);",
]

# Editable constants from the spec (§2). Stored, not hard-coded.
CONFIG_SEED = [
    ('cogs_pct',              0.45,   'Cost of goods, fraction of AOV'),
    ('clearance_cogs_pct',    1.00,   'COGS for clearance products'),
    ('kept_rate',             0.50,   'Share of orders kept after cancels/returns'),
    ('fixed_cost_per_day',    10000,  'Fixed operating cost per day (BDT)'),
    ('lead_time_days',        20,     'Reorder lead time (days)'),
    ('weekly_budget',         140000, 'Total weekly ad budget (BDT)'),
    ('proven_pool_pct',       0.70,   'Share of budget to proven products'),
    ('test_pool_pct',         0.20,   'Share of budget to new/test products'),
    ('retargeting_pool_pct',  0.10,   'Share of budget to BOFU retargeting'),
    ('cover_low_days',        10,     'Stock cover below this = low stock'),
    ('cover_reorder_days',    20,     'Stock cover below this = reorder now'),
    ('drift_threshold',       0.15,   'CPP drift above this = hold'),
    ('gap_threshold',         3000,   'Gap magnitude (BDT) to trigger inc/dec'),
    ('max_change_pct',        0.20,   'Max budget change per ad set per day'),
    ('min_days_data',         2,      'Min days of data before a push is allowed'),
    ('learning_purchases_wk', 50,     'Meta learning threshold (purchases/week)'),
]

# Default status buckets (§4). Admin can edit / add custom slugs later.
STATUS_SEED = [
    ('pre-order',  'counted'), ('processing', 'counted'),
    ('in-transit', 'counted'), ('delivered',  'counted'),
    ('cancelled',  'excluded'), ('failed',     'excluded'),
    ('returned',   'returned'),
]


def run():
    with get_connection() as conn:
        print("Creating aa_product_group ...");  conn.execute(text(PRODUCT_GROUP_SQL))
        print("Creating aa_ad_product_map ..."); conn.execute(text(AD_PRODUCT_MAP_SQL))
        print("Creating aa_sku_group_map ...");  conn.execute(text(SKU_GROUP_MAP_SQL))
        print("Creating aa_meta_ad_daily ...");  conn.execute(text(META_AD_DAILY_SQL))
        print("Creating aa_product_spend_daily ..."); conn.execute(text(PRODUCT_SPEND_DAILY_SQL))
        print("Creating aa_status_map ...");      conn.execute(text(STATUS_MAP_SQL))
        print("Creating aa_config ...");          conn.execute(text(CONFIG_SQL))
        print("Creating aa_allocation_run ...");  conn.execute(text(ALLOCATION_RUN_SQL))
        print("Creating aa_allocation_row ...");  conn.execute(text(ALLOCATION_ROW_SQL))
        print("Creating aa_change_request ...");  conn.execute(text(CHANGE_REQUEST_SQL))

        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ Index skipped: {e}")

        print("Seeding aa_config ...")
        for key, value, note in CONFIG_SEED:
            conn.execute(text("""
                INSERT INTO aa_config (key, value, note) VALUES (:k, :v, :n)
                ON CONFLICT (key) DO NOTHING
            """), {'k': key, 'v': value, 'n': note})

        print("Seeding aa_status_map ...")
        for slug, bucket in STATUS_SEED:
            conn.execute(text("""
                INSERT INTO aa_status_map (status_slug, bucket) VALUES (:s, :b)
                ON CONFLICT (status_slug) DO NOTHING
            """), {'s': slug, 'b': bucket})

        conn.commit()
    print("Ad Allocator migration complete.")


if __name__ == '__main__':
    run()
