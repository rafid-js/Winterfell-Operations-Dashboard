"""
reorder_engine.py — THE reorder formula. The ONLY place it lives.

Runs every 6 hours. For each product (grouped by base name, size suffix
stripped) it computes per-size order quantities using the target-stock model,
classifies urgency, runs the kill-chain scorer on healthy-but-stale products,
suppresses dead stock, and UPSERTs one row per product into reorder_queue.

Supply Chain READS reorder_queue (never recalculates).

Run from orchestrator/:
  python -m inventory.reorder_engine
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
# Load brain/.env explicitly so CLI runs work from any working directory
# (db.py's bare load_dotenv() only finds a .env in the current dir).
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_BRAIN, '.env'))
from db import get_connection  # noqa: E402

from . import models            # noqa: E402
from . import kill_chain_scorer  # noqa: E402


def _bulk_inputs(conn):
    """Pull every input the formula needs in a handful of queries."""
    skus = conn.execute(text("""
        SELECT sku, product_name, COALESCE(size,'') AS raw_size, category,
               COALESCE(current_stock,0) AS current_stock,
               cost_price, selling_price, created_at,
               COALESCE(total_returned,0) AS total_returned
        FROM skus
        WHERE is_active = TRUE AND product_name IS NOT NULL
    """)).fetchall()

    sales = conn.execute(text("""
        SELECT oi.sku, COALESCE(SUM(oi.quantity),0) AS qty
        FROM order_items oi JOIN orders o ON o.so_number = oi.so_number
        WHERE o.order_date >= NOW() - INTERVAL '30 days'
          AND """ + models.delivered_sql() + """
        GROUP BY oi.sku
    """)).fetchall()
    sales_map = {r._mapping['sku']: int(r._mapping['qty'] or 0) for r in sales}

    waiting = conn.execute(text("""
        SELECT oi.sku, COALESCE(SUM(oi.quantity),0) AS qty
        FROM order_items oi JOIN orders o ON o.so_number = oi.so_number
        WHERE """ + models.waiting_sql() + """
        GROUP BY oi.sku
    """)).fetchall()
    wait_map = {r._mapping['sku']: int(r._mapping['qty'] or 0) for r in waiting}

    # All-time delivered units + last delivered date per sku (sell-through + recency).
    lifetime = conn.execute(text("""
        SELECT oi.sku,
               COALESCE(SUM(oi.quantity),0) AS sold,
               MAX(o.order_date)            AS last_sale
        FROM order_items oi JOIN orders o ON o.so_number = oi.so_number
        WHERE """ + models.delivered_sql() + """
        GROUP BY oi.sku
    """)).fetchall()
    sold_map = {r._mapping['sku']: int(r._mapping['sold'] or 0) for r in lifetime}
    last_map = {r._mapping['sku']: r._mapping['last_sale'] for r in lifetime}

    return skus, sales_map, wait_map, sold_map, last_map


def _group(skus):
    groups = defaultdict(list)
    for s in skus:
        m = s._mapping
        b = models.base_name(m['product_name'])
        groups[b].append(m)
    return groups


def compute_group(base, variants, sales_map, wait_map, sold_map, last_map):
    """Run the formula for one product group; return a reorder_queue row dict."""
    # Order variants by size.
    rows = []
    for v in variants:
        label = models.extract_size_label(v['product_name'], v['raw_size'])
        rows.append({**v, 'size_label': label})
    rows.sort(key=lambda r: models.size_sort_key(r['size_label']))

    sizes, size_bd, net_bd, auto_bd, wait_bd, stock_bd, sales_bd = (
        [], {}, {}, {}, {}, {}, {})
    daily_by_size = {}
    capital_at_risk = 0.0
    sold_total = 0
    returned_total = 0
    created_dates = []
    last_sale_dates = []
    stock_total = 0
    product_name = base
    category = None

    for r in rows:
        sku = r['sku']
        size = r['size_label']
        stock = int(r['current_stock'] or 0)
        s30 = sales_map.get(sku, 0)
        wait = wait_map.get(sku, 0)
        cost = float(r['cost_price'] or 0)
        category = category or r['category']
        returned_total += int(r['total_returned'] or 0)
        if r['created_at']:
            created_dates.append(r['created_at'])

        daily = s30 / 30.0
        demand = math.ceil(daily * models.COVERAGE_DAYS)
        net_need = max(0, demand - stock)
        order_qty = net_need + wait
        if s30 > 0 and order_qty < models.MIN_PER_SIZE:
            order_qty = models.MIN_PER_SIZE
        if s30 == 0:
            order_qty = 0

        sizes.append(size)
        size_bd[size] = int(order_qty)
        net_bd[size] = int(net_need)
        auto_bd[size] = int(order_qty)      # original Brain calc — frozen copy
        wait_bd[size] = int(wait)
        stock_bd[size] = stock
        sales_bd[size] = s30
        daily_by_size[size] = daily

        capital_at_risk += stock * cost
        stock_total += stock
        sold_total += sold_map.get(sku, 0)
        ls = last_map.get(sku)
        if ls:
            last_sale_dates.append(ls)

    recommended_total = sum(size_bd.values())
    total_waiting = sum(wait_bd.values())

    # days_until_stockout: fastest-selling size that has velocity.
    dus = None
    for size, daily in daily_by_size.items():
        if daily > 0:
            d = int(stock_bd[size] / daily)
            dus = d if dus is None else min(dus, d)

    # Urgency classification.
    if (dus is not None and dus <= 7) or total_waiting > 20:
        urgency = 'Critical'
    elif (dus is not None and dus <= 14) or total_waiting > 5:
        urgency = 'Rush'
    elif sum(net_bd.values()) > 0:
        urgency = 'Monitor'
    else:
        urgency = 'Healthy'

    # Sell-through + recency for kill-chain.
    sell_through = round(sold_total / (sold_total + stock_total) * 100, 2) if (sold_total + stock_total) else 0.0
    days_since_last_sale = None
    if last_sale_dates:
        newest = max(last_sale_dates)
        try:
            days_since_last_sale = (datetime.utcnow() - newest.replace(tzinfo=None)).days
        except Exception:
            days_since_last_sale = None
    # Stock age proxy: days since the oldest variant SKU was created.
    stock_age_days = None
    if created_dates:
        try:
            oldest = min(created_dates)
            stock_age_days = (datetime.utcnow() - oldest.replace(tzinfo=None)).days
        except Exception:
            stock_age_days = None

    return_rate_pct = round(returned_total / (sold_total + returned_total) * 100, 2) \
        if (sold_total + returned_total) else 0.0

    row = {
        'sku_base': base[:200],
        'product_name': (product_name or base)[:300],
        'category': category,
        'urgency': urgency,
        'recommended_total': recommended_total,
        'size_breakdown': size_bd,
        'net_need_breakdown': net_bd,
        'auto_qty_breakdown': auto_bd,
        'waiting_orders_breakdown': wait_bd,
        'current_stock_breakdown': stock_bd,
        'sales_30d_breakdown': sales_bd,
        'days_until_stockout': dus,
        'total_waiting_orders': total_waiting,
        'capital_at_risk_bdt': round(capital_at_risk, 2),
        'kill_chain_score': 0,
        'kill_chain_stage': None,
        'sell_through_pct': sell_through,
        'days_since_last_sale': days_since_last_sale,
        'stock_age_days': stock_age_days,
        'return_rate_pct': return_rate_pct,   # transient (not a column)
        'stock_total': stock_total,            # transient
        'daily_velocity': round(sum(sales_bd.values()) / 30.0, 3),  # transient
        'suppressed': False,
    }

    # Kill-chain: score any healthy product that is either low sell-through OR
    # hasn't sold recently (time-based three-strike rule must fire regardless of
    # overall sell-through ratio).
    days_no_sale = row.get('days_since_last_sale')
    should_score = (urgency == 'Healthy' and (
        sell_through < 50
        or (days_no_sale is not None and days_no_sale >= 21)
    ))
    if should_score:
        verdict = kill_chain_scorer.score_group(row)
        row.update(verdict)
        if row.get('kill_chain_stage') in ('Markdown', 'Bundle', 'Liquidate', 'Dead'):
            row['suppressed'] = True
            row['urgency'] = 'Dead'

    return row


_UPSERT = text("""
INSERT INTO reorder_queue (
    sku_base, product_name, category, urgency, recommended_total,
    size_breakdown, net_need_breakdown, auto_qty_breakdown,
    waiting_orders_breakdown, current_stock_breakdown, sales_30d_breakdown,
    days_until_stockout, total_waiting_orders, capital_at_risk_bdt,
    kill_chain_score, kill_chain_stage, sell_through_pct,
    days_since_last_sale, stock_age_days, suppressed,
    calculated_at, expires_at
) VALUES (
    :sku_base, :product_name, :category, :urgency, :recommended_total,
    CAST(:size_breakdown AS JSONB), CAST(:net_need_breakdown AS JSONB),
    CAST(:auto_qty_breakdown AS JSONB), CAST(:waiting_orders_breakdown AS JSONB),
    CAST(:current_stock_breakdown AS JSONB), CAST(:sales_30d_breakdown AS JSONB),
    :days_until_stockout, :total_waiting_orders, :capital_at_risk_bdt,
    :kill_chain_score, :kill_chain_stage, :sell_through_pct,
    :days_since_last_sale, :stock_age_days, :suppressed,
    NOW(), NOW() + INTERVAL '6 hours'
)
ON CONFLICT (sku_base) DO UPDATE SET
    product_name = EXCLUDED.product_name,
    category = EXCLUDED.category,
    urgency = EXCLUDED.urgency,
    recommended_total = EXCLUDED.recommended_total,
    size_breakdown = EXCLUDED.size_breakdown,
    net_need_breakdown = EXCLUDED.net_need_breakdown,
    auto_qty_breakdown = EXCLUDED.auto_qty_breakdown,
    waiting_orders_breakdown = EXCLUDED.waiting_orders_breakdown,
    current_stock_breakdown = EXCLUDED.current_stock_breakdown,
    sales_30d_breakdown = EXCLUDED.sales_30d_breakdown,
    days_until_stockout = EXCLUDED.days_until_stockout,
    total_waiting_orders = EXCLUDED.total_waiting_orders,
    capital_at_risk_bdt = EXCLUDED.capital_at_risk_bdt,
    kill_chain_score = EXCLUDED.kill_chain_score,
    kill_chain_stage = EXCLUDED.kill_chain_stage,
    sell_through_pct = EXCLUDED.sell_through_pct,
    days_since_last_sale = EXCLUDED.days_since_last_sale,
    stock_age_days = EXCLUDED.stock_age_days,
    suppressed = EXCLUDED.suppressed,
    calculated_at = NOW(),
    expires_at = NOW() + INTERVAL '6 hours'
""")


def _persist(conn, row):
    params = dict(row)
    for k in ('size_breakdown', 'net_need_breakdown', 'auto_qty_breakdown',
              'waiting_orders_breakdown', 'current_stock_breakdown', 'sales_30d_breakdown'):
        params[k] = json.dumps(params.get(k) or {})
    params.setdefault('kill_chain_score', 0)
    params.setdefault('kill_chain_stage', None)
    conn.execute(_UPSERT, params)


def run():
    counts = defaultdict(int)
    with get_connection() as conn:
        skus, sales_map, wait_map, sold_map, last_map = _bulk_inputs(conn)
        groups = _group(skus)
        for base, variants in groups.items():
            if not base:
                continue
            row = compute_group(base, variants, sales_map, wait_map, sold_map, last_map)
            _persist(conn, row)
            counts[row['urgency']] += 1
            # Log Watch, Markdown, Bundle, Liquidate, Dead to dead_stock_log.
            # Watch items are NOT suppressed from reorder but ARE visible in the
            # Dead Stock tab as early warnings.
            if row.get('kill_chain_stage') is not None:
                kill_chain_scorer.log_dead_stock(conn, row)

        # Summary alert.
        title = (f"Reorder engine run: {counts.get('Critical',0)} Critical, "
                 f"{counts.get('Rush',0)} Rush, {counts.get('Monitor',0)} Monitor")
        try:
            conn.execute(text("""
                INSERT INTO alerts_log (alert_type, severity, title, created_at)
                VALUES ('inventory', 'info', :t, NOW())
            """), {'t': title})
        except Exception:
            pass  # alerts_log schema variance — non-fatal
        conn.commit()

    print(f"[reorder_engine] {datetime.utcnow().isoformat()} — {title}")
    return dict(counts)


if __name__ == '__main__':
    run()
