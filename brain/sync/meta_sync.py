"""
Meta Ads → Brain sync.

Modes:
  python -m sync.meta_sync --spend                sync last 7 days ad spend → ad_spend table
  python -m sync.meta_sync --spend --days N       sync last N days (first run only)
  python -m sync.meta_sync --spend --since YYYY-MM-DD  force pull from a specific date

Incremental: once synced, always re-pulls from last sync minus 2 days (Meta adjusts
numbers retroactively). Use --since to override and force a historical backfill.
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

def _date_chunks(start: str, end: str, chunk_days: int = 365):
    """Split a date range into chunks to stay within Meta's API limits."""
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.strptime(end, '%Y-%m-%d')
    while s < e:
        chunk_end = min(s + timedelta(days=chunk_days - 1), e)
        yield s.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')
        s = chunk_end + timedelta(days=1)


def _upsert_rows(conn, rows, account_id, account_name):
    ok = err = 0
    for row in rows:
        try:
            def _f(k, r=row):
                try:
                    return float(r.get(k) or 0)
                except (ValueError, TypeError):
                    return 0.0

            def _i(k, r=row):
                try:
                    return int(r.get(k) or 0)
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
    return ok, err


def sync_spend(days: int = 7, since_override: str = None):
    print(f"\n=== Meta Ads Spend → Brain  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('meta', 'ad_spend')

    if since_override:
        start = since_override
    else:
        since = log.last_record_at()
        if since:
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

    total_ok = total_err = 0
    chunks = list(_date_chunks(start, end))

    with get_connection() as conn:
        for chunk_start, chunk_end in chunks:
            if len(chunks) > 1:
                print(f"  Chunk {chunk_start} → {chunk_end}")
            rows = meta.get_insights(since=chunk_start, until=chunk_end, level='adset')
            print(f"  {len(rows)} rows fetched")
            ok, err = _upsert_rows(conn, rows, account_id, account_name)
            total_ok += ok
            total_err += err
        conn.commit()

    print(f"\n── Summary ── synced:{total_ok}  errors:{total_err}\n")
    if total_err == 0:
        log.finish(records_synced=total_ok, last_record_at=datetime.now())
    else:
        log.error(f"{total_err} errors during Meta spend sync")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Meta Ads → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--spend', action='store_true', help='Sync ad spend into ad_spend table')
    ap.add_argument('--days', type=int, default=7,
                    help='Days to pull on first run (default: 7)')
    ap.add_argument('--since', type=str, default=None,
                    help='Force pull from this date (YYYY-MM-DD), ignoring sync log')
    args = ap.parse_args()

    if args.spend:
        sync_spend(days=args.days, since_override=args.since)
