"""
Nuport → Brain sync.
Pulls orders from Nuport OMS and upserts into the Brain's
customers + orders tables.

Usage:
  python -m sync.nuport_sync                        # last 7 days
  python -m sync.nuport_sync --days 30              # last 30 days
  python -m sync.nuport_sync --from 2026-05-01      # from a specific date
  python -m sync.nuport_sync --order SO-64662       # single order
"""
import sys
import argparse
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

sys.path.insert(0, __file__.replace('/sync/nuport_sync.py', ''))

from db import get_connection
from apis.nuport import nuport


# ── Field mapper ─────────────────────────────────────────────────────────────
# Maps Nuport order dict → Brain schema.
# If your fields differ, adjust the keys here — don't touch the rest of the script.

def map_order(o: dict) -> dict:
    items   = o.get('salesOrderItems') or o.get('lineItems') or o.get('items') or []
    cust    = o.get('distributor') or o.get('customer') or o.get('shippingAddress') or {}
    address = o.get('shippingAddress') or cust

    product_total = sum(
        float(i.get('price', 0)) * int(i.get('quantity', 1))
        for i in items
    )
    delivery_fee = float(
        o.get('deliveryFee') or o.get('delivery_fee') or o.get('shippingCharge') or 0
    )
    discount = float(o.get('discount') or o.get('discountAmount') or 0)
    total_receivable = product_total + delivery_fee - discount

    return {
        'so_number':       o.get('internalId') or o.get('salesOrderNumber') or o.get('id'),
        'nuport_order_id': str(o.get('id') or o.get('orderId') or ''),
        'nuport_status':   o.get('status') or o.get('orderStatus') or o.get('deliveryStatus'),
        'source_channel':  o.get('channel') or o.get('source') or o.get('orderSource') or 'Unknown',
        'customer_name':   cust.get('name') or cust.get('fullName') or o.get('customerName'),
        'customer_phone':  (
            cust.get('phone') or cust.get('mobile') or cust.get('contactNumber')
            or address.get('phone') or o.get('customerPhone')
        ),
        'product_total':   round(product_total, 2),
        'delivery_fee':    round(delivery_fee, 2),
        'discount_amount': round(discount, 2),
        'total_receivable': round(total_receivable, 2),
        'order_date':      _parse_dt(o.get('createdAt') or o.get('orderDate') or o.get('date')),
        'shipped_date':    _parse_dt(o.get('shippedAt') or o.get('shippedDate')),
        'delivered_date':  _parse_dt(o.get('deliveredAt') or o.get('deliveredDate')),
    }


def map_customer(o: dict) -> dict | None:
    cust    = o.get('distributor') or o.get('customer') or {}
    address = o.get('shippingAddress') or cust
    phone   = (
        cust.get('phone') or cust.get('mobile') or cust.get('contactNumber')
        or address.get('phone') or o.get('customerPhone')
    )
    if not phone:
        return None
    return {
        'phone':   str(phone).strip(),
        'name':    cust.get('name') or cust.get('fullName') or o.get('customerName'),
        'address': address.get('address') or address.get('street'),
        'city':    address.get('city') or address.get('area'),
        'district': address.get('district') or address.get('zone'),
    }


def _parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(val)[:26], fmt)
        except ValueError:
            continue
    return None


# ── Upsert helpers ────────────────────────────────────────────────────────────

UPSERT_CUSTOMER = text("""
    INSERT INTO customers (phone, name, address, city, district, updated_at)
    VALUES (:phone, :name, :address, :city, :district, NOW())
    ON CONFLICT (phone) DO UPDATE SET
        name      = COALESCE(EXCLUDED.name,     customers.name),
        address   = COALESCE(EXCLUDED.address,  customers.address),
        city      = COALESCE(EXCLUDED.city,     customers.city),
        district  = COALESCE(EXCLUDED.district, customers.district),
        updated_at = NOW()
    RETURNING id
""")

UPSERT_ORDER = text("""
    INSERT INTO orders (
        so_number, nuport_order_id, nuport_status, source_channel,
        customer_id, customer_name, customer_phone,
        product_total, delivery_fee, discount_amount, total_receivable,
        order_date, shipped_date, delivered_date,
        updated_at
    ) VALUES (
        :so_number, :nuport_order_id, :nuport_status, :source_channel,
        :customer_id, :customer_name, :customer_phone,
        :product_total, :delivery_fee, :discount_amount, :total_receivable,
        :order_date, :shipped_date, :delivered_date,
        NOW()
    )
    ON CONFLICT (so_number) DO UPDATE SET
        nuport_status    = EXCLUDED.nuport_status,
        nuport_order_id  = COALESCE(EXCLUDED.nuport_order_id, orders.nuport_order_id),
        customer_id      = COALESCE(EXCLUDED.customer_id,     orders.customer_id),
        customer_name    = COALESCE(EXCLUDED.customer_name,   orders.customer_name),
        customer_phone   = COALESCE(EXCLUDED.customer_phone,  orders.customer_phone),
        product_total    = COALESCE(EXCLUDED.product_total,   orders.product_total),
        delivery_fee     = COALESCE(EXCLUDED.delivery_fee,    orders.delivery_fee),
        discount_amount  = COALESCE(EXCLUDED.discount_amount, orders.discount_amount),
        total_receivable = COALESCE(EXCLUDED.total_receivable,orders.total_receivable),
        shipped_date     = COALESCE(EXCLUDED.shipped_date,    orders.shipped_date),
        delivered_date   = COALESCE(EXCLUDED.delivered_date,  orders.delivered_date),
        updated_at       = NOW()
""")


# ── Main sync ─────────────────────────────────────────────────────────────────

def sync_order(conn, raw: dict) -> str:
    so = raw.get('internalId') or raw.get('salesOrderNumber') or raw.get('id', '?')

    cust_data = map_customer(raw)
    customer_id = None
    if cust_data and cust_data.get('phone'):
        row = conn.execute(UPSERT_CUSTOMER, cust_data).fetchone()
        customer_id = row[0] if row else None
        conn.commit()

    order_data = map_order(raw)
    order_data['customer_id'] = customer_id
    conn.execute(UPSERT_ORDER, order_data)
    conn.commit()
    return str(so)


def run(days: int = 7, from_date: str = None, single_order: str = None):
    print(f"\n=== Nuport → Brain Sync  {datetime.now():%Y-%m-%d %H:%M} ===\n")

    inserted = skipped = errors = 0

    with get_connection() as conn:

        if single_order:
            print(f"Syncing single order: {single_order}")
            raw = nuport.get_order(single_order)
            try:
                so = sync_order(conn, raw)
                print(f"  ✓ {so}")
                inserted = 1
            except Exception as e:
                print(f"  ✗ {single_order}: {e}")
                errors = 1

        else:
            if from_date:
                start = datetime.strptime(from_date, '%Y-%m-%d')
            else:
                start = datetime.utcnow() - timedelta(days=days)

            date_str = start.strftime('%Y-%m-%d')
            print(f"Pulling orders from {date_str} onward...")

            # Try common Nuport filter params — adjust if yours differ
            filter_keys = [
                {'from_date': date_str},
                {'startDate': date_str},
                {'created_after': date_str},
                {},                             # no filter — last resort
            ]

            order_iter = None
            for fk in filter_keys:
                try:
                    # Peek to see if it works
                    test = nuport.list_orders(page=1, limit=1, **fk)
                    if isinstance(test, list) or any(
                        k in test for k in ('data', 'orders', 'results')
                    ):
                        order_iter = nuport.iter_all_orders(**fk)
                        print(f"  Using filter params: {fk or '(none)'}")
                        break
                except Exception:
                    continue

            if order_iter is None:
                print("✗ Could not get orders from list endpoint.")
                print("  → Run: python nuport_explore.py")
                print("  → Paste the output so we can fix the filter params.")
                return

            for raw in order_iter:
                so = raw.get('internalId') or raw.get('salesOrderNumber') or raw.get('id', '?')
                try:
                    sync_order(conn, raw)
                    inserted += 1
                    print(f"  ✓ {so}")
                except Exception as e:
                    errors += 1
                    print(f"  ✗ {so}: {e}")

    print(f"\n── Summary ──────────────────────────────")
    print(f"  Upserted : {inserted}")
    print(f"  Errors   : {errors}")
    print(f"  Done at  : {datetime.now():%H:%M:%S}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Sync Nuport orders into the Brain')
    p.add_argument('--days',  type=int, default=7,  help='Pull last N days (default 7)')
    p.add_argument('--from',  dest='from_date',     help='Pull from date: YYYY-MM-DD')
    p.add_argument('--order', dest='order',         help='Sync a single SO number')
    args = p.parse_args()
    run(days=args.days, from_date=args.from_date, single_order=args.order)
