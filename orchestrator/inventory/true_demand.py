"""
true_demand.py — what customers ACTUALLY wanted vs what we delivered.

Runs daily. For the current calendar month, per product (size suffix stripped)
it separates real demand from fulfilled demand and surfaces the "ghost revenue"
— money customers tried to give us that never converted into a delivered sale
(cancelled / on-hold / pending lines).

  true_demand   = delivered + cancelled + waiting   (genuine intent to buy)
  ghost_revenue = revenue of cancelled + waiting lines (demand we failed to bank)

Writes one row per product into true_demand_log for the month, replacing the
previous month-to-date snapshot so the figure never double-counts. The metrics
bar reads SUM(ghost_revenue_bdt) for the current month.

Run from orchestrator/:
  python -m inventory.true_demand
"""
import os
import sys
from datetime import datetime

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_BRAIN, '.env'))
from db import get_connection  # noqa: E402

from . import models  # noqa: E402


_QUERY = text("""
    SELECT
      TRIM(regexp_replace(COALESCE(s.product_name, oi.product_name), :re, '', 'i')) AS sku_base,
      MAX(COALESCE(s.product_name, oi.product_name))                AS product_name,
      MAX(s.category)                                               AS category,
      COALESCE(SUM(oi.quantity), 0)                                 AS placed,
      COALESCE(SUM(oi.quantity) FILTER (WHERE """ + models.delivered_sql() + """), 0) AS delivered,
      COALESCE(SUM(oi.quantity) FILTER (WHERE """ + models.cancelled_sql() + """), 0) AS cancelled,
      COALESCE(SUM(oi.quantity) FILTER (WHERE """ + models.returned_sql()  + """), 0) AS returned,
      COALESCE(SUM(oi.quantity) FILTER (WHERE """ + models.waiting_sql()   + """), 0) AS waiting,
      COALESCE(SUM(oi.total_price) FILTER (WHERE """ + models.delivered_sql() + """), 0) AS delivered_rev,
      COALESCE(SUM(oi.total_price) FILTER (WHERE (""" + models.cancelled_sql() +
              " OR " + models.waiting_sql() + """)), 0) AS ghost_rev
    FROM order_items oi
    JOIN orders o ON o.so_number = oi.so_number
    LEFT JOIN skus s ON oi.sku = s.sku
    WHERE o.order_date >= date_trunc('month', CURRENT_DATE)
      AND oi.product_name IS NOT NULL
      AND oi.product_name !~ '^[0-9][0-9.,\\s]*$'
    GROUP BY sku_base
    HAVING TRIM(regexp_replace(COALESCE(s.product_name, oi.product_name), :re, '', 'i')) <> ''
""")

_INSERT = text("""
    INSERT INTO true_demand_log (
      sku_base, size, period_start, period_end,
      orders_placed, orders_delivered, orders_cancelled, orders_returned,
      true_demand, ghost_revenue_bdt, stockout_days, lost_sales_bdt, calculated_at
    ) VALUES (
      :sku_base, NULL, date_trunc('month', CURRENT_DATE), CURRENT_DATE,
      :placed, :delivered, :cancelled, :returned,
      :true_demand, :ghost, :stockout_days, :lost_sales, NOW()
    )
""")


def run():
    written = 0
    total_ghost = 0.0
    with get_connection() as conn:
        rows = conn.execute(_QUERY, {'re': models._SIZE_RE_SQL}).fetchall()

        # Replace this month's snapshot (idempotent across daily re-runs).
        conn.execute(text(
            "DELETE FROM true_demand_log WHERE period_start = date_trunc('month', CURRENT_DATE)"
        ))

        for r in rows:
            m = r._mapping
            delivered = int(m['delivered'] or 0)
            cancelled = int(m['cancelled'] or 0)
            waiting   = int(m['waiting'] or 0)
            returned  = int(m['returned'] or 0)
            true_demand = delivered + cancelled + waiting
            ghost = float(m['ghost_rev'] or 0)
            # No historical stock snapshots — lost_sales is the ghost figure,
            # stockout_days left 0 until a stock-history feed exists.
            conn.execute(_INSERT, {
                'sku_base': m['sku_base'][:200],
                'placed': int(m['placed'] or 0),
                'delivered': delivered,
                'cancelled': cancelled,
                'returned': returned,
                'true_demand': true_demand,
                'ghost': round(ghost, 2),
                'stockout_days': 0,
                'lost_sales': round(ghost, 2),
            })
            written += 1
            total_ghost += ghost

        conn.commit()

    msg = f"true_demand: {written} products, ghost revenue BDT {round(total_ghost):,}"
    print(f"[true_demand] {datetime.utcnow().isoformat()} — {msg}")
    return {'products': written, 'ghost_revenue_bdt': round(total_ghost, 2)}


if __name__ == '__main__':
    run()
