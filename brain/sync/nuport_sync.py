"""
Nuport → Brain sync.

Modes:
  python -m sync.nuport_sync --products          sync all products  → skus table
  python -m sync.nuport_sync --inventory         sync stock levels  → skus table
  python -m sync.nuport_sync --order SO-XXXXX    upsert one order   → orders + customers

Notes:
- Nuport has NO list-orders endpoint; orders are synced individually by SO number.
- Inventory uses page=-1 to fetch everything in one call.
- Phone number is the universal customer key across all systems.
- SO number (SO-XXXXX) is the universal order key across all systems.
- SKU is the universal product key across all systems.
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])

from db import get_connection
from apis.nuport import nuport
from sync.sync_log import SyncLog


# ── Field mappers ─────────────────────────────────────────────────────────────

def _parse_dt(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(val)[:26], fmt)
        except ValueError:
            continue
    return None


def _specs(product: dict) -> dict:
    """Extract size/color from productSpecifications into a plain dict."""
    out = {}
    for s in product.get('productSpecifications') or []:
        label = (s.get('specification', {}).get('label') or '').lower().strip()
        out[label] = s.get('value')
    return out


def map_sku_from_inventory(item: dict) -> dict | None:
    p = item.get('product') or {}
    sku = (p.get('sku') or '').strip()
    if not sku:
        return None
    specs = _specs(p)
    return {
        'sku':               sku,
        'product_name':      p.get('name'),
        'category':          p.get('category'),
        'color':             specs.get('color'),
        'size':              specs.get('size'),
        'selling_price':     float(p.get('price') or 0) or None,
        'cost_price':        float(p.get('purchasePrice') or 0) or None,
        'current_stock':     int(item.get('quantity') or 0),
        'nuport_product_id': p.get('id'),
        'is_active':         not p.get('deleted', False),
    }


def map_sku_from_product(p: dict) -> dict | None:
    sku = (p.get('sku') or '').strip()
    if not sku:
        return None
    specs = _specs(p)
    return {
        'sku':               sku,
        'product_name':      p.get('name'),
        'color':             specs.get('color'),
        'size':              specs.get('size'),
        'selling_price':     float(p.get('price') or 0) or None,
        'nuport_product_id': p.get('id'),
        'is_active':         not p.get('deleted', False),
    }


def map_order(o: dict) -> dict:
    items = o.get('salesOrderItems') or []
    dist  = o.get('distributor') or {}

    product_total = sum(
        float(i.get('price', 0)) * int(i.get('quantity', 1))
        for i in items
    )
    delivery_fee = float(o.get('deliveryCharge') or 0)
    discount     = float(o.get('discountAmount') or 0)

    return {
        'so_number':        o.get('internalId'),
        'nuport_order_id':  o.get('id'),
        'nuport_status':    o.get('status'),
        'source_channel':   o.get('source'),
        'customer_name':    dist.get('name'),
        'customer_phone':   dist.get('phone'),
        'product_total':    round(product_total, 2),
        'delivery_fee':     round(delivery_fee, 2),
        'discount_amount':  round(discount, 2),
        'total_receivable': round(product_total + delivery_fee - discount, 2),
        'order_date':       _parse_dt(o.get('orderDate') or o.get('createdAt')),
        'shipped_date':     _parse_dt(o.get('shippedAt')),
        'delivered_date':   _parse_dt(o.get('deliveredAt')),
        'pathao_waybill':   o.get('deliveryTrackingId') or o.get('deliveryConsignment'),
    }


def map_customer(o: dict) -> dict | None:
    dist  = o.get('distributor') or {}
    phone = (dist.get('phone') or '').strip()
    if not phone:
        return None
    locs = dist.get('locations') or []
    loc  = locs[0] if locs else {}
    return {
        'phone':    phone,
        'name':     dist.get('name'),
        'address':  loc.get('address'),
        'city':     loc.get('label'),
        'district': loc.get('district'),
    }


# ── Upsert SQL ────────────────────────────────────────────────────────────────

UPSERT_SKU = text("""
    INSERT INTO skus (
        sku, product_name, category, color, size,
        selling_price, cost_price, current_stock,
        nuport_product_id, is_active, updated_at
    ) VALUES (
        :sku, :product_name, :category, :color, :size,
        :selling_price, :cost_price, :current_stock,
        :nuport_product_id, :is_active, NOW()
    )
    ON CONFLICT (sku) DO UPDATE SET
        product_name      = COALESCE(EXCLUDED.product_name,      skus.product_name),
        category          = COALESCE(EXCLUDED.category,          skus.category),
        color             = COALESCE(EXCLUDED.color,             skus.color),
        size              = COALESCE(EXCLUDED.size,              skus.size),
        selling_price     = COALESCE(EXCLUDED.selling_price,     skus.selling_price),
        cost_price        = COALESCE(EXCLUDED.cost_price,        skus.cost_price),
        current_stock     = COALESCE(EXCLUDED.current_stock,     skus.current_stock),
        nuport_product_id = COALESCE(EXCLUDED.nuport_product_id, skus.nuport_product_id),
        is_active         = EXCLUDED.is_active,
        updated_at        = NOW()
""")

UPSERT_SKU_STOCK_ONLY = text("""
    INSERT INTO skus (sku, product_name, current_stock, nuport_product_id, updated_at)
    VALUES (:sku, :product_name, :current_stock, :nuport_product_id, NOW())
    ON CONFLICT (sku) DO UPDATE SET
        current_stock     = EXCLUDED.current_stock,
        product_name      = COALESCE(skus.product_name, EXCLUDED.product_name),
        nuport_product_id = COALESCE(skus.nuport_product_id, EXCLUDED.nuport_product_id),
        updated_at        = NOW()
""")

UPSERT_CUSTOMER = text("""
    INSERT INTO customers (phone, name, address, city, district, updated_at)
    VALUES (:phone, :name, :address, :city, :district, NOW())
    ON CONFLICT (phone) DO UPDATE SET
        name       = COALESCE(EXCLUDED.name,     customers.name),
        address    = COALESCE(EXCLUDED.address,  customers.address),
        city       = COALESCE(EXCLUDED.city,     customers.city),
        district   = COALESCE(EXCLUDED.district, customers.district),
        updated_at = NOW()
    RETURNING id
""")

UPSERT_ORDER = text("""
    INSERT INTO orders (
        so_number, nuport_order_id, nuport_status, source_channel,
        customer_id, customer_name, customer_phone,
        product_total, delivery_fee, discount_amount, total_receivable,
        pathao_waybill, order_date, shipped_date, delivered_date, updated_at
    ) VALUES (
        :so_number, :nuport_order_id, :nuport_status, :source_channel,
        :customer_id, :customer_name, :customer_phone,
        :product_total, :delivery_fee, :discount_amount, :total_receivable,
        :pathao_waybill, :order_date, :shipped_date, :delivered_date, NOW()
    )
    ON CONFLICT (so_number) DO UPDATE SET
        nuport_status    = EXCLUDED.nuport_status,
        nuport_order_id  = COALESCE(EXCLUDED.nuport_order_id,  orders.nuport_order_id),
        source_channel   = COALESCE(EXCLUDED.source_channel,   orders.source_channel),
        customer_id      = COALESCE(EXCLUDED.customer_id,      orders.customer_id),
        customer_name    = COALESCE(EXCLUDED.customer_name,    orders.customer_name),
        customer_phone   = COALESCE(EXCLUDED.customer_phone,   orders.customer_phone),
        product_total    = COALESCE(EXCLUDED.product_total,    orders.product_total),
        delivery_fee     = COALESCE(EXCLUDED.delivery_fee,     orders.delivery_fee),
        discount_amount  = COALESCE(EXCLUDED.discount_amount,  orders.discount_amount),
        total_receivable = COALESCE(EXCLUDED.total_receivable, orders.total_receivable),
        pathao_waybill   = COALESCE(EXCLUDED.pathao_waybill,   orders.pathao_waybill),
        shipped_date     = COALESCE(EXCLUDED.shipped_date,     orders.shipped_date),
        delivered_date   = COALESCE(EXCLUDED.delivered_date,   orders.delivered_date),
        updated_at       = NOW()
""")


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_products():
    print(f"\n=== Nuport Products → Brain SKUs  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('nuport', 'products')
    ok = skip = err = 0

    with get_connection() as conn:
        for p in nuport.iter_all_products():
            row = map_sku_from_product(p)
            if not row:
                skip += 1
                continue
            try:
                row.setdefault('category', None)
                row.setdefault('cost_price', None)
                row.setdefault('current_stock', 0)
                conn.execute(UPSERT_SKU, row)
                conn.commit()
                ok += 1
                print(f"  ✓ {row['sku']:<20} {row['product_name']}")
            except Exception as e:
                err += 1
                print(f"  ✗ {row.get('sku','?')}: {e}")

    print(f"\n── Summary ── upserted:{ok}  skipped(no SKU):{skip}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok)
    else:
        log.error(f"{err} errors during product sync")


def sync_inventory(updated_from: str = None):
    print(f"\n=== Nuport Inventory → Brain SKUs  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('nuport', 'inventory')

    # Use last successful sync date if no manual override
    if not updated_from:
        since = log.last_record_at()
        if since:
            updated_from = since.strftime('%Y-%m-%d')
            print(f"  Incremental: updatedFrom {updated_from} (last sync)")
        else:
            print("  Full sync — no previous sync found")
    else:
        print(f"  Incremental: updatedFrom {updated_from} (manual)")

    ok = skip = err = 0
    newest_dt = None

    with get_connection() as conn:
        items = nuport.get_all_inventory(updated_from=updated_from)
        print(f"  {len(items)} inventory records fetched from Nuport\n")

        for item in items:
            row = map_sku_from_inventory(item)
            if not row:
                skip += 1
                continue
            try:
                conn.execute(UPSERT_SKU, row)
                conn.commit()
                ok += 1
                status = 'OUT' if row['current_stock'] <= 0 else str(row['current_stock'])
                print(f"  ✓ {row['sku']:<20} stock: {status:>6}  {row['product_name']}")
            except Exception as e:
                err += 1
                print(f"  ✗ {row.get('sku','?')}: {e}")

    print(f"\n── Summary ── upserted:{ok}  skipped(no SKU):{skip}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=datetime.now())
    else:
        log.error(f"{err} errors during inventory sync")


def sync_single_order(so_number: str):
    print(f"\n=== Nuport Order Sync: {so_number} ===\n")

    raw = nuport.get_order(so_number)
    if not raw.get('internalId'):
        print(f"✗ Order {so_number} not found in Nuport")
        return

    with get_connection() as conn:
        # Upsert customer first (phone is universal key)
        cust = map_customer(raw)
        customer_id = None
        if cust:
            row = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
            customer_id = row[0] if row else None
            conn.commit()
            print(f"  ✓ Customer: {cust['name']} ({cust['phone']})")

        # Upsert order
        order = map_order(raw)
        order['customer_id'] = customer_id
        conn.execute(UPSERT_ORDER, order)
        conn.commit()
        print(f"  ✓ Order {order['so_number']} → status: {order['nuport_status']}")
        if order['pathao_waybill']:
            print(f"    Pathao waybill: {order['pathao_waybill']}")

    print()


def sync_orders_from_brain():
    """Pull current Nuport status for every SO number already in the Brain."""
    print(f"\n=== Nuport Status Refresh — all Brain orders  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    ok = err = 0

    with get_connection() as conn:
        rows = conn.execute(text(
            "SELECT so_number FROM orders WHERE so_number IS NOT NULL ORDER BY order_date DESC"
        )).fetchall()
        so_numbers = [r[0] for r in rows]

    if not so_numbers:
        print("No orders in Brain yet. Run a product/inventory sync or add orders first.")
        return

    print(f"  {len(so_numbers)} orders to refresh\n")
    with get_connection() as conn:
        for so in so_numbers:
            try:
                raw = nuport.get_order(so)
                if not raw.get('internalId'):
                    continue
                cust = map_customer(raw)
                customer_id = None
                if cust:
                    row = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
                    customer_id = row[0] if row else None
                    conn.commit()
                order = map_order(raw)
                order['customer_id'] = customer_id
                conn.execute(UPSERT_ORDER, order)
                conn.commit()
                ok += 1
                print(f"  ✓ {so} → {order['nuport_status']}")
            except Exception as e:
                err += 1
                print(f"  ✗ {so}: {e}")

    print(f"\n── Summary ── refreshed:{ok}  errors:{err}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Nuport → Brain sync')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--products',       action='store_true', help='Sync products → skus')
    g.add_argument('--inventory',      action='store_true', help='Sync inventory → skus')
    g.add_argument('--order',          metavar='SO_NUMBER', help='Sync one order by SO number')
    g.add_argument('--refresh-orders', action='store_true', help='Refresh Nuport status for all Brain orders')
    p.add_argument('--from', dest='from_date', metavar='YYYY-MM-DD',
                   help='Incremental date filter (inventory only)')
    args = p.parse_args()

    if args.products:
        sync_products()
    elif args.inventory:
        sync_inventory(updated_from=args.from_date)
    elif args.order:
        sync_single_order(args.order)
    elif args.refresh_orders:
        sync_orders_from_brain()
