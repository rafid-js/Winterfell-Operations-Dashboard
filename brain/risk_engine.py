"""
risk_engine.py — Supply Chain risk recalculator.

Scans all active purchase orders, evaluates overdue/at-risk conditions,
updates po_status, and writes alert entries to po_timeline.

Run on a schedule (e.g. every 6 hours via cron or Railway cron job):
    python brain/risk_engine.py
"""
import os
import sys
from datetime import date, datetime

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from db import get_connection  # noqa: E402


RISK_THRESHOLD_DAYS = 5   # days before due → flag At Risk
DELAYED_THRESHOLD_DAYS = 0  # past due date → Delayed


def _tl_id(conn):
    row = conn.execute(text(
        "SELECT COALESCE(MAX(CAST(SUBSTRING(event_id FROM 4) AS INTEGER)), 0) + 1 AS nxt "
        "FROM po_timeline WHERE event_id LIKE 'TL-%'"
    )).fetchone()
    return 'TL-' + str(row.nxt if row else 1).zfill(4)


def run():
    today = date.today()
    updated = 0
    alerted = 0

    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT po_id, product_name, due_date, po_status, current_stage
            FROM purchase_orders
            WHERE po_status NOT IN ('Completed', 'Cancelled')
            ORDER BY due_date ASC NULLS LAST
        """)).fetchall()

        for row in rows:
            po_id = row.po_id
            due_raw = row.due_date
            old_status = row.po_status or 'Active'

            if due_raw is None:
                continue

            due = due_raw if isinstance(due_raw, date) else date.fromisoformat(str(due_raw)[:10])
            days_remaining = (due - today).days

            if days_remaining < DELAYED_THRESHOLD_DAYS:
                new_status = 'Delayed'
            elif days_remaining <= RISK_THRESHOLD_DAYS:
                new_status = 'At Risk'
            else:
                new_status = 'Active'

            if new_status != old_status:
                conn.execute(text("""
                    UPDATE purchase_orders
                    SET po_status = :ns, updated_at = NOW()
                    WHERE po_id = :po_id
                """), {'ns': new_status, 'po_id': po_id})
                updated += 1

                title = ('PO is now Delayed — ' if new_status == 'Delayed'
                         else 'PO flagged At Risk — ')
                note = (str(abs(days_remaining)) + ' days overdue'
                        if days_remaining < 0
                        else str(days_remaining) + ' days until due')

                eid = _tl_id(conn)
                conn.execute(text("""
                    INSERT INTO po_timeline
                        (event_id, po_id, stage, event_title, event_note,
                         source_type, is_alert, logged_by)
                    VALUES
                        (:eid, :po_id, :stage, :title, :note,
                         'brain', TRUE, 'Risk Engine')
                """), {
                    'eid': eid, 'po_id': po_id,
                    'stage': row.current_stage or 'PO Issued',
                    'title': title + po_id,
                    'note': note,
                })
                alerted += 1

        conn.commit()

    print(f'[risk_engine] {datetime.utcnow().isoformat()} — '
          f'checked {len(rows)} POs, updated {updated}, alerted {alerted}')


if __name__ == '__main__':
    run()
