"""Ad Allocator — per-product Meta spend sync (catalog / Advantage+).

Pulls daily account insights broken down by catalog product (spend +
omni_purchase conversions + value) and upserts into aa_product_spend_daily.
retailer_id (the WooCommerce variation ID) is the join key to skus.wc_variation_id.

Products that don't resolve to a brain SKU (e.g. ragnarbd.com items) simply fall
into "Unmapped" downstream — no filtering needed.

Run from orchestrator/:
  python -m ad_allocator.product_sync --days 30
"""
import argparse
import os
import sys
from datetime import date, timedelta

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from db import get_connection  # noqa: E402
from apis.meta import meta  # noqa: E402

from .data import RAGNAR_ACCOUNT_ID  # noqa: E402

UPSERT = text("""
    INSERT INTO aa_product_spend_daily
      (date, account_id, retailer_id, product_name, spend_bdt, purchases,
       purchase_value, updated_at)
    VALUES (:date, :acct, :rid, :name, :spend, :purchases, :pvalue, NOW())
    ON CONFLICT (date, retailer_id) DO UPDATE SET
      product_name = EXCLUDED.product_name, spend_bdt = EXCLUDED.spend_bdt,
      purchases = EXCLUDED.purchases, purchase_value = EXCLUDED.purchase_value,
      updated_at = NOW()
""")


def _purchases(row):
    cnt = val = 0.0
    for a in row.get('actions', []) or []:
        if a.get('action_type') in ('omni_purchase', 'purchase'):
            cnt = float(a.get('value') or 0); break
    for a in row.get('action_values', []) or []:
        if a.get('action_type') in ('omni_purchase', 'purchase'):
            val = float(a.get('value') or 0); break
    return int(cnt), val


def _split_product_id(pid):
    """Meta formats product_id as '<retailer_id>, <product name>'."""
    pid = (pid or '').strip()
    if not pid:
        return None, None
    rid, _, name = pid.partition(',')
    return rid.strip(), name.strip()


def run(days=30, since=None):
    until = date.today()
    start = date.fromisoformat(since) if since else until - timedelta(days=days - 1)
    rows = meta.get_product_insights(start.isoformat(), until.isoformat())

    n = skipped = 0
    with get_connection() as conn:
        for r in rows:
            rid, name = _split_product_id(r.get('product_id'))
            if not rid:
                skipped += 1
                continue
            cnt, val = _purchases(r)
            conn.execute(UPSERT, {
                'date': r.get('date_start'), 'acct': RAGNAR_ACCOUNT_ID,
                'rid': rid[:80], 'name': (name or '')[:300],
                'spend': float(r.get('spend') or 0), 'purchases': cnt, 'pvalue': val,
            })
            n += 1
        conn.commit()
    msg = f"product_sync: {n} product-day rows ({skipped} no retailer_id) {start} → {until}"
    print(f"[aa_product_sync] {msg}")
    return {'rows': n, 'skipped': skipped, 'since': start.isoformat(), 'until': until.isoformat()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--since')
    a = ap.parse_args()
    run(a.days, a.since)
