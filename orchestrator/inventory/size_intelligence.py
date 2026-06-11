"""
size_intelligence.py — learn the natural size curve per category.

Runs weekly. From the last 180 days of DELIVERED sales it computes, for every
category, the share each size takes of that category's volume. Future POs can
then be split by the size ratios customers actually buy instead of a flat guess.

Writes size_profiles (full rebuild each run): one row per (category, size) with
its distribution_pct and the sample_size it was learned from.

Run from orchestrator/:
  python -m inventory.size_intelligence
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_BRAIN, '.env'))
from db import get_connection  # noqa: E402

from . import models  # noqa: E402


# Pull delivered quantity per (category, product_name, size) so we can resolve
# the size label the same way the rest of the module does.
_QUERY = text("""
    SELECT
      COALESCE(s.category, 'Uncategorised')      AS category,
      COALESCE(s.product_name, oi.product_name)  AS product_name,
      COALESCE(s.size, '')                       AS raw_size,
      COALESCE(SUM(oi.quantity), 0)              AS qty
    FROM order_items oi
    JOIN orders o ON o.so_number = oi.so_number
    LEFT JOIN skus s ON oi.sku = s.sku
    WHERE """ + models.delivered_sql() + """
      AND o.order_date >= NOW() - INTERVAL '180 days'
      AND oi.product_name IS NOT NULL
    GROUP BY 1, 2, 3
""")

_INSERT = text("""
    INSERT INTO size_profiles (category, size, distribution_pct, sample_size, calculated_at)
    VALUES (:category, :size, :pct, :sample, NOW())
""")


def run():
    # category -> size -> qty
    cat_size = defaultdict(lambda: defaultdict(int))
    with get_connection() as conn:
        rows = conn.execute(_QUERY).fetchall()
        for r in rows:
            m = r._mapping
            size = models.extract_size_label(m['product_name'], m['raw_size'])
            cat_size[m['category']][size] += int(m['qty'] or 0)

        conn.execute(text("DELETE FROM size_profiles"))

        written = 0
        for category, sizes in cat_size.items():
            total = sum(sizes.values())
            if total <= 0:
                continue
            for size, qty in sizes.items():
                conn.execute(_INSERT, {
                    'category': (category or '')[:100],
                    'size': (size or '—')[:20],
                    'pct': round(qty / total * 100, 2),
                    'sample': total,
                })
                written += 1
        conn.commit()

    msg = f"size_intelligence: {len(cat_size)} categories, {written} size rows"
    print(f"[size_intelligence] {datetime.utcnow().isoformat()} — {msg}")
    return {'categories': len(cat_size), 'rows': written}


if __name__ == '__main__':
    run()
