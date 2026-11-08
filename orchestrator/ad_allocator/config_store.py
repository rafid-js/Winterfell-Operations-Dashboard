"""Ad Allocator — config + status-map access (spec §2, §4).

Constants live in aa_config (editable, not hard-coded). status buckets live in
aa_status_map. Both fall back to the seeded defaults if the row is missing, so
the engine never crashes on a fresh / partial DB.
"""
from sqlalchemy import text

DEFAULTS = {
    'cogs_pct': 0.45, 'clearance_cogs_pct': 1.00, 'kept_rate': 0.50,
    'fixed_cost_per_day': 10000, 'lead_time_days': 20, 'weekly_budget': 140000,
    'proven_pool_pct': 0.70, 'test_pool_pct': 0.20, 'retargeting_pool_pct': 0.10,
    'cover_low_days': 10, 'cover_reorder_days': 20, 'drift_threshold': 0.15,
    'gap_threshold': 3000, 'max_change_pct': 0.20, 'min_days_data': 2,
    'learning_purchases_wk': 50,
}


def load_config(conn) -> dict:
    cfg = dict(DEFAULTS)
    try:
        rows = conn.execute(text("SELECT key, value FROM aa_config")).fetchall()
        for r in rows:
            cfg[r._mapping['key']] = float(r._mapping['value'])
    except Exception as e:  # noqa: BLE001 — fresh DB before migration
        print(f'  ⚠ aa_config unavailable, using defaults: {e}', flush=True)
    return cfg


def set_config(conn, key: str, value: float):
    conn.execute(text("""
        INSERT INTO aa_config (key, value, updated_at) VALUES (:k, :v, NOW())
        ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
    """), {'k': key, 'v': value})
    conn.commit()


def counted_slugs(conn) -> list[str]:
    """Status slugs whose bucket counts toward orders (counted + returned —
    returned orders are counted then trimmed by the kept-rate, per §4)."""
    try:
        rows = conn.execute(text(
            "SELECT status_slug FROM aa_status_map WHERE bucket IN ('counted','returned')"
        )).fetchall()
        slugs = [r._mapping['status_slug'] for r in rows]
        if slugs:
            return slugs
    except Exception:  # noqa: BLE001
        pass
    return ['pre-order', 'processing', 'in-transit', 'delivered', 'returned']


def status_map(conn) -> list[dict]:
    rows = conn.execute(text(
        "SELECT status_slug, bucket FROM aa_status_map ORDER BY bucket, status_slug"
    )).fetchall()
    return [dict(r._mapping) for r in rows]


def set_status_bucket(conn, slug: str, bucket: str):
    conn.execute(text("""
        INSERT INTO aa_status_map (status_slug, bucket, updated_at) VALUES (:s, :b, NOW())
        ON CONFLICT (status_slug) DO UPDATE SET bucket = :b, updated_at = NOW()
    """), {'s': slug, 'b': bucket})
    conn.commit()
