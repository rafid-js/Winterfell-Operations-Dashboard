"""
Zoho Books → Brain sync.

Modes:
  python -m sync.zoho_sync --invoices   sync invoices → financials table
  python -m sync.zoho_sync --payments   sync payments → financials table

Incremental by default — reads last_record_at from sync_log.
Matches orders via so_number (Zoho invoice number = Nuport SO number).
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])

from db import get_connection
from apis.zoho import zoho
from sync.sync_log import SyncLog

import os
ORG = os.getenv('ZOHO_ORG_ID', '')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _float(val):
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _dt(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(val)[:19], fmt)
        except ValueError:
            continue
    return None


def _paginate(path: str, key: str, last_modified: str = None) -> list:
    """Fetch all pages from a Zoho Books endpoint."""
    page = 1
    results = []
    while True:
        params = {'organization_id': ORG, 'page': page, 'per_page': 200}
        if last_modified:
            params['last_modified_time'] = last_modified
        data = zoho.get(path, params)
        items = data.get(key, [])
        results.extend(items)
        if not data.get('page_context', {}).get('has_more_page', False):
            break
        page += 1
    return results


# ── SQL ───────────────────────────────────────────────────────────────────────

UPSERT_FINANCIAL = text("""
    INSERT INTO financials (
        so_number, zoho_invoice_id,
        invoiced_amount, collected_amount, payout_amount,
        difference, reconciled, zoho_marked_paid, zoho_paid_at,
        reconcile_status, created_at
    ) VALUES (
        :so_number, :zoho_invoice_id,
        :invoiced_amount, :collected_amount, :payout_amount,
        :difference, :reconciled, :zoho_marked_paid, :zoho_paid_at,
        :reconcile_status, NOW()
    )
    ON CONFLICT (so_number) DO UPDATE SET
        zoho_invoice_id  = COALESCE(EXCLUDED.zoho_invoice_id,  financials.zoho_invoice_id),
        invoiced_amount  = COALESCE(EXCLUDED.invoiced_amount,  financials.invoiced_amount),
        collected_amount = COALESCE(EXCLUDED.collected_amount, financials.collected_amount),
        payout_amount    = COALESCE(EXCLUDED.payout_amount,    financials.payout_amount),
        difference       = COALESCE(EXCLUDED.difference,       financials.difference),
        reconciled       = EXCLUDED.reconciled,
        zoho_marked_paid = EXCLUDED.zoho_marked_paid,
        zoho_paid_at     = COALESCE(EXCLUDED.zoho_paid_at,     financials.zoho_paid_at),
        reconcile_status = EXCLUDED.reconcile_status
""")

UPDATE_ORDER_ZOHO = text("""
    UPDATE orders SET
        zoho_invoice_id = :zoho_invoice_id,
        payment_status  = :payment_status,
        updated_at      = NOW()
    WHERE so_number = :so_number
      AND zoho_invoice_id IS NULL
""")


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_invoices():
    print(f"\n=== Zoho Books Invoices → Brain  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('zoho', 'invoices')
    since = log.last_record_at()
    last_modified = since.strftime('%Y-%m-%dT%H:%M:%S+0000') if since else None

    if last_modified:
        print(f"  Incremental: modified after {last_modified}")
    else:
        print("  Full sync — no previous sync found")

    invoices = _paginate('/invoices', 'invoices', last_modified)
    print(f"  {len(invoices)} invoices fetched\n")

    ok = skip = err = 0
    newest_dt = since

    with get_connection() as conn:
        for inv in invoices:
            so = (inv.get('invoice_number') or '').strip()
            if not so:
                skip += 1
                continue
            try:
                total     = _float(inv.get('total'))
                paid      = _float(inv.get('payment_made')) or 0
                balance   = _float(inv.get('balance')) or 0
                status    = inv.get('status', '')
                is_paid   = status == 'paid'
                paid_at   = _dt(inv.get('last_payment_date')) if is_paid else None
                diff      = round((paid or 0) - (total or 0), 2) if total else None

                fin = {
                    'so_number':        so,
                    'zoho_invoice_id':  inv.get('invoice_id'),
                    'invoiced_amount':  total,
                    'collected_amount': paid,
                    'payout_amount':    paid,
                    'difference':       diff,
                    'reconciled':       is_paid,
                    'zoho_marked_paid': is_paid,
                    'zoho_paid_at':     paid_at,
                    'reconcile_status': status,
                }
                conn.execute(UPSERT_FINANCIAL, fin)
                conn.execute(UPDATE_ORDER_ZOHO, {
                    'so_number':       so,
                    'zoho_invoice_id': inv.get('invoice_id'),
                    'payment_status':  status,
                })
                conn.commit()
                ok += 1
                print(f"  ✓ {so:<20} {status:<10} total:{total}")

                mod = inv.get('last_modified_time')
                if mod:
                    mod_dt = _dt(mod)
                    if mod_dt and (newest_dt is None or mod_dt > newest_dt):
                        newest_dt = mod_dt

            except Exception as e:
                err += 1
                conn.rollback()
                print(f"  ✗ {so}: {e}")

    print(f"\n── Summary ── synced:{ok}  skipped:{skip}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=newest_dt)
    else:
        log.error(f"{err} errors during invoice sync")


def sync_payments():
    print(f"\n=== Zoho Books Payments → Brain  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('zoho', 'payments')
    since = log.last_record_at()
    last_modified = since.strftime('%Y-%m-%dT%H:%M:%S+0000') if since else None

    if last_modified:
        print(f"  Incremental: modified after {last_modified}")
    else:
        print("  Full sync — no previous sync found")

    payments = _paginate('/customerpayments', 'customerpayments', last_modified)
    print(f"  {len(payments)} payments fetched\n")

    ok = skip = err = 0
    newest_dt = since

    with get_connection() as conn:
        for pmt in payments:
            invoices = pmt.get('invoices') or []
            if not invoices:
                skip += 1
                continue
            try:
                for inv_ref in invoices:
                    so = (inv_ref.get('invoice_number') or '').strip()
                    if not so:
                        continue
                    amount = _float(inv_ref.get('amount_applied')) or _float(pmt.get('amount'))
                    conn.execute(text("""
                        UPDATE financials
                        SET collected_amount = :amount,
                            zoho_marked_paid = TRUE,
                            zoho_paid_at     = :paid_at,
                            reconciled       = TRUE,
                            reconcile_status = 'paid'
                        WHERE so_number = :so_number
                    """), {
                        'amount':    amount,
                        'paid_at':   _dt(pmt.get('date')),
                        'so_number': so,
                    })
                conn.commit()
                ok += 1

                mod = pmt.get('last_modified_time')
                if mod:
                    mod_dt = _dt(mod)
                    if mod_dt and (newest_dt is None or mod_dt > newest_dt):
                        newest_dt = mod_dt

            except Exception as e:
                err += 1
                conn.rollback()
                print(f"  ✗ payment {pmt.get('payment_id')}: {e}")

    print(f"\n── Summary ── synced:{ok}  skipped:{skip}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=newest_dt)
    else:
        log.error(f"{err} errors during payment sync")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Zoho Books → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--invoices', action='store_true', help='Sync invoices → financials')
    g.add_argument('--payments', action='store_true', help='Sync payments → financials')
    args = ap.parse_args()

    if args.invoices:
        sync_invoices()
    elif args.payments:
        sync_payments()
