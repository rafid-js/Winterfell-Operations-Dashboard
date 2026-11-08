"""Ad Allocator — per-AD Meta spend sync (level=ad).

Pulls daily ad-level insights (spend + omni_purchase conversions + value) and
upserts into aa_meta_ad_daily, so the allocator can attribute spend to product
groups through aa_ad_product_map by meta_ad_id.

Run from orchestrator/:
  python -m ad_allocator.ad_sync --days 30
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
    INSERT INTO aa_meta_ad_daily
      (date, account_id, ad_id, ad_name, adset_id, adset_name,
       campaign_id, campaign_name, spend_bdt, purchases, purchase_value, updated_at)
    VALUES (:date, :acct, :ad_id, :ad_name, :adset_id, :adset_name,
            :campaign_id, :campaign_name, :spend, :purchases, :pvalue, NOW())
    ON CONFLICT (date, ad_id) DO UPDATE SET
      ad_name = EXCLUDED.ad_name, adset_id = EXCLUDED.adset_id,
      adset_name = EXCLUDED.adset_name, campaign_id = EXCLUDED.campaign_id,
      campaign_name = EXCLUDED.campaign_name, spend_bdt = EXCLUDED.spend_bdt,
      purchases = EXCLUDED.purchases, purchase_value = EXCLUDED.purchase_value,
      updated_at = NOW()
""")


def _purchases(row):
    """Extract omni_purchase count + value from the actions arrays."""
    cnt = val = 0.0
    for a in row.get('actions', []) or []:
        if a.get('action_type') in ('omni_purchase', 'purchase'):
            cnt = float(a.get('value') or 0); break
    for a in row.get('action_values', []) or []:
        if a.get('action_type') in ('omni_purchase', 'purchase'):
            val = float(a.get('value') or 0); break
    return int(cnt), val


def run(days=30, since=None):
    until = date.today()
    start = date.fromisoformat(since) if since else until - timedelta(days=days - 1)
    rows = meta.get_ad_insights(start.isoformat(), until.isoformat())

    n = 0
    with get_connection() as conn:
        for r in rows:
            cnt, val = _purchases(r)
            conn.execute(UPSERT, {
                'date': r.get('date_start'), 'acct': RAGNAR_ACCOUNT_ID,
                'ad_id': r.get('ad_id'), 'ad_name': (r.get('ad_name') or '')[:300],
                'adset_id': r.get('adset_id'), 'adset_name': (r.get('adset_name') or '')[:300],
                'campaign_id': r.get('campaign_id'),
                'campaign_name': (r.get('campaign_name') or '')[:300],
                'spend': float(r.get('spend') or 0), 'purchases': cnt, 'pvalue': val,
            })
            n += 1
        conn.commit()
    msg = f"ad_sync: {n} ad-day rows upserted ({start} → {until})"
    print(f"[aa_ad_sync] {msg}")
    return {'rows': n, 'since': start.isoformat(), 'until': until.isoformat()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--since')
    a = ap.parse_args()
    run(a.days, a.since)
