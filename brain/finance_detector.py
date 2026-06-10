"""
finance_detector.py — Zoho Books finance event detector.

Scans recent Zoho Books payments and expenses for PO-XXXX references
in the description/notes fields, then auto-inserts matching po_timeline
events so finance activity appears on the Supply Chain timeline.

Run on a schedule (e.g. every 6 hours via cron or Railway cron job):
    python brain/finance_detector.py

Required env vars (in brain/.env):
    ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ORG_ID
"""
import os
import re
import sys
from datetime import datetime, timezone

import requests
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from db import get_connection  # noqa: E402

# Zoho OAuth token endpoint
_TOKEN_URL = 'https://accounts.zoho.com/oauth/v2/token'
_API_BASE  = 'https://www.zohoapis.com/books/v3'

PO_PATTERN = re.compile(r'\bPO-\d{4,}\b', re.IGNORECASE)


# ── Zoho auth ─────────────────────────────────────────────────────────────────

def _get_access_token():
    resp = requests.post(_TOKEN_URL, data={
        'grant_type':    'refresh_token',
        'client_id':     os.environ['ZOHO_CLIENT_ID'],
        'client_secret': os.environ['ZOHO_CLIENT_SECRET'],
        'refresh_token': os.environ['ZOHO_REFRESH_TOKEN'],
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()['access_token']


def _zoho_get(path, token, org_id, params=None):
    url = _API_BASE + path
    headers = {'Authorization': 'Zoho-oauthtoken ' + token}
    p = {'organization_id': org_id}
    if params:
        p.update(params)
    r = requests.get(url, headers=headers, params=p, timeout=20)
    r.raise_for_status()
    return r.json()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _tl_id(conn):
    row = conn.execute(text(
        "SELECT COALESCE(MAX(CAST(SUBSTRING(event_id FROM 4) AS INTEGER)), 0) + 1 AS nxt "
        "FROM po_timeline WHERE event_id LIKE 'TL-%'"
    )).fetchone()
    return 'TL-' + str(row.nxt if row else 1).zfill(4)


def _already_logged(conn, zoho_ref):
    """Return True if a finance event with this Zoho reference was already inserted."""
    row = conn.execute(text(
        "SELECT 1 FROM po_timeline WHERE source_ref = :ref LIMIT 1"
    ), {'ref': zoho_ref}).fetchone()
    return row is not None


def _po_exists(conn, po_id):
    row = conn.execute(text(
        "SELECT 1 FROM purchase_orders WHERE po_id = :po_id"
    ), {'po_id': po_id}).fetchone()
    return row is not None


def _insert_event(conn, po_id, stage, title, note, amount, ref, event_date):
    eid = _tl_id(conn)
    conn.execute(text("""
        INSERT INTO po_timeline
            (event_id, po_id, stage, event_title, event_note,
             amount_bdt, source_type, source_ref, logged_by, event_date)
        VALUES
            (:eid, :po_id, :stage, :title, :note,
             :amount, 'finance', :ref, 'Zoho Books', :edate)
    """), {
        'eid': eid, 'po_id': po_id, 'stage': stage,
        'title': title, 'note': note, 'amount': amount,
        'ref': ref, 'edate': event_date,
    })
    return eid


# ── scanning logic ────────────────────────────────────────────────────────────

def _extract_po_ids(text_val):
    if not text_val:
        return []
    return list({m.upper() for m in PO_PATTERN.findall(str(text_val))})


def scan_payments(conn, token, org_id):
    inserted = 0
    try:
        data = _zoho_get('/customerpayments', token, org_id,
                         {'sort_column': 'created_time', 'sort_order': 'D', 'per_page': 100})
        payments = data.get('customerpayments') or []
    except Exception as e:
        print(f'  [payments] fetch error: {e}')
        return 0

    for pmt in payments:
        ref = 'zpmt-' + str(pmt.get('payment_id', ''))
        if _already_logged(conn, ref):
            continue

        needle = ' '.join(filter(None, [
            pmt.get('description'), pmt.get('reference_number'),
            pmt.get('notes'), pmt.get('payment_number'),
        ]))
        po_ids = _extract_po_ids(needle)

        for po_id in po_ids:
            if not _po_exists(conn, po_id):
                continue
            amount = float(pmt.get('amount') or 0)
            edate  = pmt.get('date') or datetime.now(timezone.utc).isoformat()
            title  = 'Payment received — ' + (pmt.get('payment_number') or ref)
            note   = ('Customer payment of ৳{:,.0f}'.format(amount)
                      + (' ref: ' + pmt.get('reference_number') if pmt.get('reference_number') else ''))
            stage  = _current_stage(conn, po_id)
            eid = _insert_event(conn, po_id, stage, title, note, amount, ref, edate)
            print(f'    inserted {eid} for {po_id} (payment {ref})')
            inserted += 1

    return inserted


def scan_expenses(conn, token, org_id):
    inserted = 0
    try:
        data = _zoho_get('/expenses', token, org_id,
                         {'sort_column': 'created_time', 'sort_order': 'D', 'per_page': 100})
        expenses = data.get('expenses') or []
    except Exception as e:
        print(f'  [expenses] fetch error: {e}')
        return 0

    for exp in expenses:
        ref = 'zexp-' + str(exp.get('expense_id', ''))
        if _already_logged(conn, ref):
            continue

        needle = ' '.join(filter(None, [
            exp.get('description'), exp.get('reference_number'),
            exp.get('notes'),
        ]))
        po_ids = _extract_po_ids(needle)

        for po_id in po_ids:
            if not _po_exists(conn, po_id):
                continue
            amount = float(exp.get('total') or exp.get('amount') or 0)
            edate  = exp.get('date') or datetime.now(timezone.utc).isoformat()
            title  = 'Expense logged — ' + (exp.get('account_name') or 'Expense')
            note   = ('৳{:,.0f}'.format(amount)
                      + (' · ' + exp.get('description') if exp.get('description') else ''))
            stage  = _current_stage(conn, po_id)
            eid = _insert_event(conn, po_id, stage, title, note, amount, ref, edate)
            print(f'    inserted {eid} for {po_id} (expense {ref})')
            inserted += 1

    return inserted


def _current_stage(conn, po_id):
    row = conn.execute(text(
        "SELECT current_stage FROM purchase_orders WHERE po_id = :po_id"
    ), {'po_id': po_id}).fetchone()
    return (row.current_stage if row else None) or 'PO Issued'


# ── main ──────────────────────────────────────────────────────────────────────

def run():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

    required = ['ZOHO_CLIENT_ID', 'ZOHO_CLIENT_SECRET', 'ZOHO_REFRESH_TOKEN', 'ZOHO_ORG_ID']
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f'[finance_detector] Missing env vars: {missing}. Skipping.')
        return

    org_id = os.environ['ZOHO_ORG_ID']
    token  = _get_access_token()

    total = 0
    with get_connection() as conn:
        total += scan_payments(conn, token, org_id)
        total += scan_expenses(conn, token, org_id)
        conn.commit()

    print(f'[finance_detector] {datetime.utcnow().isoformat()} — inserted {total} timeline events')


if __name__ == '__main__':
    run()
