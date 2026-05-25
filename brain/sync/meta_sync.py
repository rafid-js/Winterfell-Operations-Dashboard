"""
Meta Ads → Brain sync.

Modes:
  python -m sync.meta_sync --spend          sync last 7 days ad spend → ad_spend table
  python -m sync.meta_sync --spend --days N sync last N days

Incremental: always re-pulls last 7 days (Meta adjusts numbers retroactively),
then records the newest date in sync_log.
"""
import sys
import argparse
from datetime import datetime, timedelta
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])

from db import get_connection
from apis.meta import meta
from sync.sync_log import SyncLog


# ── SQL ───────────────────────────────────────────────────────────────────────

UPSERT_SPEND = text("""
    INSERT INTO ad_spend (
        date, account_id, account_name,
        campaign_id, campaign_name,
        adset_id, adset_name,
        spend_bdt, impressions, clicks, ctr, cpm, cpc,
        created_at
    ) VALUES (
        :date, :account_id, :account_name,
        :campaign_id, :campaign_name,
        :adset_id, :adset_name,
        :spend_bdt, :impressions, :clicks, :ctr, :cpm, :cpc,
        NOW()
    )
    ON CONFLICT (date, campaign_id, adset_id) DO UPDATE SET
        spend_bdt   = EXCLUDED.spend_bdt,
        impressions = EXCLUDED.impressions,
        clicks      = EXCLUDED.clicks,
        ctr         = EXCLUDED.ctr,
        cpm         = EXCLUDED.cpm,
        cpc         = EXCLUDED.cpc,
        account_name = COALESCE(EXCLUDED.account_name, ad_spend.account_name),
        campaign_name = COALESCE(EXCLUDED.campaign_name, ad_spend.campaign_name),
        adset_name   = COALESCE(EXCLUDED.adset_name, ad_spend.adset_name)
""")


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_spend(days: int = 7):
    print(f"\n=== Meta Ads Spend → Brain  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('meta', 'ad_spend')

    since = log.last_record_at()
    if since:
        # Re-pull from last sync minus 2 days (Meta adjusts numbers retroactively)
        start = (since - timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    end = datetime.now().strftime('%Y-%m-%d')
    print(f"  Pulling {start} → {end}\n")

    try:
        account = meta.get_account_info()
        account_name = account.get('name', '')
        account_id   = account.get('id', '')
    except Exception:
        account_name = ''
        account_id   = ''

    rows = meta.get_insights(since=start, until=end, level='adset')
    print(f"  {len(rows)} insight rows fetched\n")

    ok = err = 0

    with get_connection() as conn:
        for row in rows:
            try:
                def _f(k):
                    try:
                        return float(row.get(k) or 0)
                    except (ValueError, TypeError):
                        return 0.0

                def _i(k):
                    try:
                        return int(row.get(k) or 0)
                    except (ValueError, TypeError):
                        return 0

                conn.execute(UPSERT_SPEND, {
                    'date':          row.get('date_start'),
                    'account_id':    account_id,
                    'account_name':  account_name,
                    'campaign_id':   row.get('campaign_id'),
                    'campaign_name': row.get('campaign_name'),
                    'adset_id':      row.get('adset_id'),
                    'adset_name':    row.get('adset_name'),
                    'spend_bdt':     _f('spend'),
                    'impressions':   _i('impressions'),
                    'clicks':        _i('clicks'),
                    'ctr':           _f('ctr'),
                    'cpm':           _f('cpm'),
                    'cpc':           _f('cpc'),
                })
                ok += 1
            except Exception as e:
                err += 1
                print(f"  ✗ {row.get('date_start')} {row.get('campaign_name')}: {e}")

        conn.commit()

    print(f"\n── Summary ── synced:{ok}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=datetime.now())
    else:
        log.error(f"{err} errors during Meta spend sync")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Meta Ads → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--spend', action='store_true', help='Sync ad spend into ad_spend table')
    ap.add_argument('--days', type=int, default=7,
                    help='Days to pull on first run (default: 7)')
    args = ap.parse_args()

    if args.spend:
        sync_spend(days=args.days)
