"""
test_batch.py — evaluate small "test" production runs of new products.

Runs daily. For every SKU flagged batch_type='Test', it measures the day-7
sell-through (units delivered in the first 7 days after the test date / test
quantity) and assigns a verdict:

  Winner    >= 60% sold in 7 days  -> scale up, reorder big
  Promising 30-60%                 -> repeat at the same size
  Kill      < 30%                  -> do not reorder
  Pending   < 7 days elapsed       -> still measuring

The verdict + sell-through are written back onto the skus row so the reorder
engine and the UI can act on them.

Run from orchestrator/:
  python -m inventory.test_batch
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

WINNER_THRESHOLD = 60.0
PROMISING_THRESHOLD = 30.0


def _verdict(sell_through, days_elapsed):
    if days_elapsed < 7:
        return 'Pending'
    if sell_through >= WINNER_THRESHOLD:
        return 'Winner'
    if sell_through >= PROMISING_THRESHOLD:
        return 'Promising'
    return 'Kill'


# Units delivered within the first 7 days of the test window, per SKU.
_SOLD_7D = text("""
    SELECT COALESCE(SUM(oi.quantity), 0) AS qty
    FROM order_items oi
    JOIN orders o ON o.so_number = oi.so_number
    WHERE oi.sku = :sku
      AND """ + models.delivered_sql() + """
      AND o.order_date >= :start
      AND o.order_date <  :start + INTERVAL '7 days'
""")


def run():
    updated = 0
    verdicts = {}
    with get_connection() as conn:
        tests = conn.execute(text("""
            SELECT sku, product_name, test_batch_date, COALESCE(test_batch_qty, 0) AS qty
            FROM skus
            WHERE batch_type = 'Test' AND test_batch_date IS NOT NULL
        """)).fetchall()

        for t in tests:
            m = t._mapping
            qty = int(m['qty'] or 0)
            start = m['test_batch_date']
            days_elapsed = (datetime.utcnow().date() - start).days
            sold7 = conn.execute(_SOLD_7D, {'sku': m['sku'], 'start': start}).scalar() or 0
            sell_through = round(sold7 / qty * 100, 2) if qty > 0 else 0.0
            verdict = _verdict(sell_through, days_elapsed)

            conn.execute(text("""
                UPDATE skus
                SET test_day7_sellthrough = :st, test_verdict = :v, updated_at = NOW()
                WHERE sku = :sku
            """), {'st': sell_through, 'v': verdict, 'sku': m['sku']})
            updated += 1
            verdicts[verdict] = verdicts.get(verdict, 0) + 1

        conn.commit()

    msg = f"test_batch: {updated} test SKUs evaluated — {verdicts}"
    print(f"[test_batch] {datetime.utcnow().isoformat()} — {msg}")
    return {'evaluated': updated, 'verdicts': verdicts}


if __name__ == '__main__':
    run()
