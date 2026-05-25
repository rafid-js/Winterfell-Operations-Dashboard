"""
Pathao → Brain sync.

Modes:
  python -m sync.pathao_sync --poll     poll status for all active waybills in Brain
  python -m sync.pathao_sync --webhook  print webhook setup instructions

Pathao is primarily webhook-driven for real-time status updates.
The --poll mode is a fallback to refresh waybills that may have been missed.

Webhook endpoint (add to server.js):
  POST /webhooks/pathao  → calls pathao.verify_webhook() then updates pathao_waybills
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])

from db import get_connection
from apis.pathao import pathao
from sync.sync_log import SyncLog


# ── Status classifier ─────────────────────────────────────────────────────────

_ACTIVE_STATUSES = {
    'Pending', 'Pickup_Requested', 'Picked_Up',
    'In_Transit', 'At_Hub', 'Out_For_Delivery',
    'Partially_Delivered', 'On_Hold',
}

_LOSS_STATUSES = {
    'Lost', 'Damaged',
}

_RETURN_STATUSES = {
    'Return_In_Transit', 'Return_Received',
    'Cancelled', 'Returned_to_Merchant',
}

DAYS_THRESHOLD = 10  # flag as anomaly if in transit > 10 days


# ── SQL ───────────────────────────────────────────────────────────────────────

UPSERT_WAYBILL = text("""
    INSERT INTO pathao_waybills (
        waybill_number, so_number, current_status, previous_status,
        last_location, failure_reason,
        days_in_transit, is_lost, anomaly_flag, anomaly_reason,
        updated_at
    ) VALUES (
        :waybill_number, :so_number, :current_status, :previous_status,
        :last_location, :failure_reason,
        :days_in_transit, :is_lost, :anomaly_flag, :anomaly_reason,
        NOW()
    )
    ON CONFLICT (waybill_number) DO UPDATE SET
        previous_status = pathao_waybills.current_status,
        current_status  = EXCLUDED.current_status,
        last_location   = COALESCE(EXCLUDED.last_location,  pathao_waybills.last_location),
        failure_reason  = COALESCE(EXCLUDED.failure_reason, pathao_waybills.failure_reason),
        days_in_transit = EXCLUDED.days_in_transit,
        is_lost         = EXCLUDED.is_lost,
        is_damaged      = EXCLUDED.is_lost,
        anomaly_flag    = EXCLUDED.anomaly_flag,
        anomaly_reason  = EXCLUDED.anomaly_reason,
        updated_at      = NOW()
""")


def _classify(status: str, order_date, days: int) -> dict:
    is_lost    = status in _LOSS_STATUSES
    anomaly    = is_lost or (days > DAYS_THRESHOLD and status in _ACTIVE_STATUSES)
    reason     = None
    if is_lost:
        reason = f'Status: {status}'
    elif anomaly:
        reason = f'{days} days in transit with status: {status}'
    return {'is_lost': is_lost, 'anomaly_flag': anomaly, 'anomaly_reason': reason}


# ── Sync functions ────────────────────────────────────────────────────────────

def poll_active_waybills():
    """Refresh status for all non-delivered waybills stored in the Brain."""
    print(f"\n=== Pathao Waybill Poll  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('pathao', 'waybills')

    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT waybill_number, so_number, current_status, created_at
            FROM pathao_waybills
            WHERE current_status NOT IN (
                'Delivered', 'Returned_to_Merchant', 'Cancelled',
                'Return_Received', 'Lost', 'Damaged'
            )
            ORDER BY created_at DESC
        """)).fetchall()

    if not rows:
        print("  No active waybills to poll.\n")
        log.finish(records_synced=0)
        return

    print(f"  {len(rows)} active waybills to poll\n")
    ok = err = 0

    with get_connection() as conn:
        for waybill, so_number, cur_status, created_at in rows:
            try:
                data = pathao.get_order(waybill)
                new_status = (data.get('delivery_status') or data.get('order_status') or '').strip()
                location   = data.get('location') or data.get('last_location')
                reason     = data.get('reason') or data.get('failure_reason')

                days = (datetime.now() - created_at).days if created_at else 0
                flags = _classify(new_status, created_at, days)

                conn.execute(UPSERT_WAYBILL, {
                    'waybill_number': waybill,
                    'so_number':      so_number,
                    'current_status': new_status or cur_status,
                    'previous_status': cur_status,
                    'last_location':  location,
                    'failure_reason': reason,
                    'days_in_transit': days,
                    **flags,
                })
                conn.commit()
                ok += 1

                flag = ' ⚠ ANOMALY' if flags['anomaly_flag'] else ''
                print(f"  ✓ {waybill:<20} {new_status}{flag}")

            except Exception as e:
                err += 1
                print(f"  ✗ {waybill}: {e}")

    print(f"\n── Summary ── polled:{ok}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=datetime.now())
    else:
        log.error(f"{err} errors during waybill poll")


def process_webhook(payload: dict, signature: str = None) -> bool:
    """
    Called by server.js when Pathao sends a webhook.
    Returns True if processed successfully.
    """
    if signature and not pathao.verify_webhook(
        str(payload).encode(), signature
    ):
        print("  ✗ Webhook signature invalid")
        return False

    order     = payload.get('order') or payload
    waybill   = (order.get('consignment_id') or '').strip()
    so_number = None
    status    = (order.get('order_status') or order.get('delivery_status') or '').strip()
    location  = order.get('location')
    reason    = order.get('reason')

    if not waybill:
        return False

    # Look up so_number from existing waybill record
    with get_connection() as conn:
        row = conn.execute(text(
            "SELECT so_number, created_at FROM pathao_waybills WHERE waybill_number = :w"
        ), {'w': waybill}).fetchone()
        if row:
            so_number  = row[0]
            created_at = row[1]
        else:
            created_at = datetime.now()

        days  = (datetime.now() - created_at).days if created_at else 0
        flags = _classify(status, created_at, days)

        conn.execute(UPSERT_WAYBILL, {
            'waybill_number':  waybill,
            'so_number':       so_number,
            'current_status':  status,
            'previous_status': None,
            'last_location':   location,
            'failure_reason':  reason,
            'days_in_transit': days,
            **flags,
        })
        conn.commit()

    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Pathao → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--poll',    action='store_true', help='Poll all active waybills')
    g.add_argument('--webhook', action='store_true', help='Print webhook setup info')
    args = ap.parse_args()

    if args.poll:
        poll_active_waybills()
    elif args.webhook:
        print("\nPathao webhook setup:")
        print("  1. In Pathao dashboard → Settings → Webhooks")
        print("  2. Set callback URL to: https://your-domain.com/webhooks/pathao")
        print("  3. Set webhook secret = PATHAO_WEBHOOK_SECRET in brain/.env")
        print("  4. The server.js /webhooks/pathao route calls pathao_sync.process_webhook()")
        print()
