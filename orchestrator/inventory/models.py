"""
Inventory — data access layer.

All functions return plain Python dicts / lists of dicts (Decimal -> float,
date/datetime -> ISO strings) so results are JSON-serialisable straight out of
the route handlers. Queries use parameterised text() — never string-format user
input into SQL.

This module owns THE reorder formula's inputs (status definitions, size
grouping). reorder_engine.py computes and writes reorder_queue; the routes and
the Supply Chain bridge READ from reorder_queue — they never recalculate.
"""
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
# Load brain/.env explicitly so CLI runs (python -m inventory.*) work from any
# directory — db.py's bare load_dotenv() only finds a .env in the current dir.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_BRAIN, '.env'))
from db import get_connection  # noqa: E402


# ── formula configuration ────────────────────────────────────────────────────
# Aligned with the Supply Chain module: 15-day production + 10-day forward
# runway = 25 days of demand coverage. These live HERE only.
DEFAULT_LEAD_TIME_DAYS = 15
TARGET_RUNWAY_DAYS     = 10
COVERAGE_DAYS          = DEFAULT_LEAD_TIME_DAYS + TARGET_RUNWAY_DAYS
MIN_PER_SIZE           = 5

# ── status vocabulary ────────────────────────────────────────────────────────
# nuport_status holds raw Nuport values (mixed case; COMPLETED aliased to
# DELIVERED on import). These snippets mirror app.py / the live dashboard so the
# numbers match what the team already sees elsewhere.
#
#   DELIVERED  -> a real, completed sale (used for velocity + sell-through)
#   WAITING    -> Pending or On Hold customers still owed product
#   CANCELLED  -> voided
#   RETURNED   -> returned / flagged / damaged
SQL_DELIVERED = "UPPER(COALESCE({c}, '')) IN ('DELIVERED', 'COMPLETED')"
SQL_WAITING   = ("(UPPER(COALESCE({c}, '')) IN ('PENDING', 'REQUESTED') "
                 "OR COALESCE({c}, '') ILIKE 'on%hold')")
SQL_CANCELLED = "COALESCE({c}, '') ILIKE '%cancel%'"
SQL_RETURNED  = ("(COALESCE({c}, '') ILIKE '%return%' "
                 "OR COALESCE({c}, '') ILIKE '%flag%' "
                 "OR COALESCE({c}, '') ILIKE '%damag%')")


def delivered_sql(col='o.nuport_status'):
    return SQL_DELIVERED.format(c=col)


def waiting_sql(col='o.nuport_status'):
    return SQL_WAITING.format(c=col)


def cancelled_sql(col='o.nuport_status'):
    return SQL_CANCELLED.format(c=col)


def returned_sql(col='o.nuport_status'):
    return SQL_RETURNED.format(c=col)


# ── size grouping (same regex the Products / Supply Chain modules use) ────────
_SIZE_RE_SQL = (
    r'\s*-\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[2-5][0-9])'
    r'(\s*\([^)]*\))?\s*$'
)
_SIZE_RE_PY = re.compile(_SIZE_RE_SQL, re.IGNORECASE)

SIZE_ORDER = {
    'XXS': 0, '2XS': 0, 'XS': 1, 'S': 2, 'M': 3, 'L': 4, 'XL': 5,
    'XXL': 6, '2XL': 6, 'XXXL': 7, '3XL': 7, '4XL': 8, '5XL': 9,
    'FREE': 10, 'OS': 10, 'ONE SIZE': 10,
}


def base_name(product_name):
    """Strip the size suffix to get the grouping key, e.g. 'Cargo Pants - 32' -> 'Cargo Pants'."""
    return _SIZE_RE_PY.sub('', (product_name or '').strip()).strip()


def extract_size_label(product_name, raw_size):
    if raw_size and str(raw_size).strip():
        return str(raw_size).strip()
    m = _SIZE_RE_PY.search(product_name or '')
    return m.group(1) if m else (product_name or '—')


def size_sort_key(size):
    s = (size or '').strip().upper()
    if s.isdigit():
        return (100, int(s), s)
    return (SIZE_ORDER.get(s, 50), 0, s)


def size_type_for(sizes):
    """'bottom' if any size is a numeric waist (30/32…), else 'top'."""
    for s in sizes:
        if str(s).strip().isdigit():
            return 'bottom'
    return 'top'


# ── serialisation helpers ────────────────────────────────────────────────────
def _conv(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _row_to_dict(row):
    m = row._mapping
    return {k: _conv(m[k]) for k in m.keys()}


def _rows_to_list(rows):
    return [_row_to_dict(r) for r in rows]


# ── reads (UI + bridge) ───────────────────────────────────────────────────────
def get_reorder_queue(urgency=None, include_suppressed=False):
    """Reorder rows, optionally filtered by urgency. Suppressed rows excluded by default."""
    clauses = []
    params = {}
    if not include_suppressed:
        clauses.append("suppressed = FALSE")
    if urgency:
        clauses.append("LOWER(urgency) = :urg")
        params['urg'] = urgency.lower()
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM reorder_queue""" + where + """
            ORDER BY
              CASE urgency
                WHEN 'Critical' THEN 1 WHEN 'Rush' THEN 2
                WHEN 'Monitor' THEN 3 WHEN 'Healthy' THEN 4 ELSE 5 END,
              days_until_stockout ASC NULLS LAST,
              total_waiting_orders DESC
        """), params).fetchall()
    return _rows_to_list(rows)


def get_suppressed():
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM reorder_queue WHERE suppressed = TRUE
            ORDER BY kill_chain_score DESC NULLS LAST
        """)).fetchall()
    return _rows_to_list(rows)


def get_stock_health():
    """Every reorder_queue row (all urgencies, incl. suppressed) for the Stock Health tab."""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM reorder_queue
            ORDER BY
              CASE urgency
                WHEN 'Critical' THEN 1 WHEN 'Rush' THEN 2
                WHEN 'Monitor' THEN 3 WHEN 'Healthy' THEN 4 ELSE 5 END,
              product_name ASC
        """)).fetchall()
    return _rows_to_list(rows)


def get_dead_stock():
    """Active kill-chain rows from dead_stock_log, newest first."""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM dead_stock_log
            WHERE status <> 'Cleared'
            ORDER BY
              CASE kill_chain_stage
                WHEN 'Dead' THEN 1 WHEN 'Liquidate' THEN 2 WHEN 'Bundle' THEN 3
                WHEN 'Markdown' THEN 4 WHEN 'Watch' THEN 5 ELSE 6 END,
              kill_chain_score DESC NULLS LAST
        """)).fetchall()
    return _rows_to_list(rows)


def get_metrics():
    """Summary counts for the metrics bar + Radar nav card."""
    with get_connection() as conn:
        q = conn.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE urgency='Critical' AND NOT suppressed) AS critical,
              COUNT(*) FILTER (WHERE urgency='Rush'     AND NOT suppressed) AS rush,
              COUNT(*) FILTER (WHERE urgency='Monitor'  AND NOT suppressed) AS monitor,
              COUNT(*) FILTER (WHERE urgency='Healthy'  AND NOT suppressed) AS healthy,
              COUNT(*) FILTER (WHERE suppressed)                            AS suppressed
            FROM reorder_queue
        """)).fetchone()
        dead = conn.execute(text("""
            SELECT
              COUNT(*)                              AS sku_count,
              COALESCE(SUM(capital_locked_bdt), 0)  AS capital_locked
            FROM dead_stock_log WHERE status <> 'Cleared'
        """)).fetchone()
        ghost = conn.execute(text("""
            SELECT COALESCE(SUM(ghost_revenue_bdt), 0) AS ghost
            FROM true_demand_log
            WHERE period_start >= date_trunc('month', CURRENT_DATE)
        """)).fetchone()
    m = _row_to_dict(q)
    m['dead_stock_count'] = _conv(dead._mapping['sku_count'])
    m['capital_locked'] = _conv(dead._mapping['capital_locked'])
    m['ghost_revenue'] = _conv(ghost._mapping['ghost'])
    return m


def get_po_prefill(sku_base):
    """Build the Supply Chain PO pre-fill payload from the stored reorder row.
    Reads only — never recalculates. Returns None if the row is missing."""
    with get_connection() as conn:
        row = conn.execute(text(
            "SELECT * FROM reorder_queue WHERE sku_base = :b"
        ), {'b': sku_base}).fetchone()
        if row is None:
            return None
        r = _row_to_dict(row)

        # Preferred supplier: most reliable supplier with stats, if any.
        sup = conn.execute(text("""
            SELECT id, name, avg_lead_days, reliability_score
            FROM suppliers
            WHERE COALESCE(is_blacklisted, FALSE) = FALSE
            ORDER BY is_preferred DESC NULLS LAST,
                     reliability_score DESC NULLS LAST
            LIMIT 1
        """)).fetchone()
        supplier = _row_to_dict(sup) if sup else None

        # Per-size cost lookup for the size grid (best-effort).
        sizes = list((r.get('size_breakdown') or {}).keys())
        sizes.sort(key=size_sort_key)

    lead = (supplier or {}).get('avg_lead_days') or DEFAULT_LEAD_TIME_DAYS
    try:
        from datetime import timedelta
        due = (date.today() + timedelta(days=int(lead))).isoformat()
    except Exception:
        due = None

    return {
        'sku_base': r.get('sku_base'),
        'product_name': r.get('product_name'),
        'category': r.get('category'),
        'size_type': size_type_for(sizes),
        'sizes': sizes,
        'auto_qty': r.get('size_breakdown') or {},
        'current_stock': r.get('current_stock_breakdown') or {},
        'sales_30d': r.get('sales_30d_breakdown') or {},
        'net_need': r.get('net_need_breakdown') or {},
        'waiting_orders': r.get('waiting_orders_breakdown') or {},
        'recommended_total': r.get('recommended_total') or 0,
        'preferred_supplier': supplier,
        'suggested_due_date': due,
        'urgency': r.get('urgency'),
        'days_until_stockout': r.get('days_until_stockout'),
        'capital_at_risk': r.get('capital_at_risk_bdt'),
        'lead_time_days': lead,
        'runway_days': TARGET_RUNWAY_DAYS,
        'coverage_days': COVERAGE_DAYS,
    }


# ── writes (engine + UI actions) ──────────────────────────────────────────────
def mark_po_created(sku_base, po_id):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE reorder_queue SET po_created = TRUE, po_id = :pid
            WHERE sku_base = :b
        """), {'pid': po_id, 'b': sku_base})
        conn.commit()


def update_dead_stock_action(row_id, status):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE dead_stock_log
            SET status = :s,
                resolved_at = CASE WHEN :s IN ('Cleared','Written Off')
                                   THEN NOW() ELSE resolved_at END
            WHERE id = :id
        """), {'s': status, 'id': row_id})
        # When cleared, un-suppress the SKU so it can reorder again.
        if status in ('Cleared', 'Written Off'):
            conn.execute(text("""
                UPDATE reorder_queue rq
                SET suppressed = FALSE
                FROM dead_stock_log ds
                WHERE ds.id = :id AND rq.sku_base = ds.sku_base
            """), {'id': row_id})
        conn.commit()


def set_batch_type(sku, batch_type, qty=None):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE skus
            SET batch_type = :bt,
                test_batch_date = CASE WHEN :bt = 'Test' THEN COALESCE(test_batch_date, CURRENT_DATE) ELSE test_batch_date END,
                test_batch_qty  = COALESCE(:qty, test_batch_qty),
                updated_at = NOW()
            WHERE sku = :sku
        """), {'bt': batch_type, 'qty': qty, 'sku': sku})
        conn.commit()
