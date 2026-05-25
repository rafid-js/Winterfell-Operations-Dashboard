"""
WooCommerce → Brain sync.

Modes:
  python -m sync.wc_sync --orders       sync orders + line items + customers
  python -m sync.wc_sync --products     sync product image URLs → skus table

Incremental by default — reads last_record_at from sync_log.
Universal keys: phone (customers), WC-{id} or matched so_number (orders), sku (products).
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])

from db import get_connection
from apis.woocommerce import wc
from sync.sync_log import SyncLog


# ── Mappers ───────────────────────────────────────────────────────────────────

def _str(val):
    v = (val or '').strip()
    return v or None


def _float(val):
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _phone(billing: dict) -> str | None:
    return _str(billing.get('phone'))


def map_customer(order: dict) -> dict | None:
    b = order.get('billing') or {}
    phone = _phone(b)
    if not phone:
        return None
    name = ' '.join(filter(None, [b.get('first_name'), b.get('last_name')])).strip() or None
    return {
        'phone':          phone,
        'name':           name,
        'email':          _str(b.get('email')),
        'address':        _str(b.get('address_1')),
        'city':           _str(b.get('city')),
        'district':       _str(b.get('state')),
        'wc_customer_id': order.get('customer_id') or None,
    }


def map_order(order: dict, customer_id: int | None) -> dict:
    b      = order.get('billing') or {}
    phone  = _phone(b)
    name   = ' '.join(filter(None, [b.get('first_name'), b.get('last_name')])).strip() or None
    wc_id  = order.get('id')
    number = str(order.get('number') or wc_id)

    shipping = _float(order.get('shipping_total'))
    discount = _float(order.get('discount_total'))
    total    = _float(order.get('total'))

    created = order.get('date_created')
    modified = order.get('date_modified')

    def _dt(s):
        if not s:
            return None
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
        return None

    return {
        'so_number':        f'WC-{wc_id}',
        'wc_order_id':      wc_id,
        'wc_order_number':  number,
        'wc_status':        _str(order.get('status')),
        'source_channel':   'woocommerce',
        'customer_id':      customer_id,
        'customer_name':    name,
        'customer_phone':   phone,
        'delivery_fee':     shipping,
        'discount_amount':  discount,
        'total_receivable': total,
        'order_date':       _dt(created),
        'updated_at':       _dt(modified),
    }


def map_items(order: dict) -> list[dict]:
    items = []
    so = f"WC-{order.get('id')}"
    for li in (order.get('line_items') or []):
        sku = _str(li.get('sku'))
        if not sku:
            continue
        qty   = li.get('quantity') or 1
        total = _float(li.get('total'))
        price = round(total / qty, 2) if total and qty else _float(li.get('price'))
        items.append({
            'so_number':            so,
            'sku':                  sku,
            'product_name':         _str(li.get('name')),
            'size':                 None,
            'color':                None,
            'quantity':             qty,
            'unit_price':           price,
            'total_price':          total,
            'item_discount':        _float(li.get('total_tax')) or 0,
            'price_after_discount': total,
        })
    return items


# ── SQL ───────────────────────────────────────────────────────────────────────

UPSERT_CUSTOMER = text("""
    INSERT INTO customers (phone, name, email, address, city, district, wc_customer_id, updated_at)
    VALUES (:phone, :name, :email, :address, :city, :district, :wc_customer_id, NOW())
    ON CONFLICT (phone) DO UPDATE SET
        name           = COALESCE(EXCLUDED.name,           customers.name),
        email          = COALESCE(EXCLUDED.email,          customers.email),
        address        = COALESCE(EXCLUDED.address,        customers.address),
        city           = COALESCE(EXCLUDED.city,           customers.city),
        district       = COALESCE(EXCLUDED.district,       customers.district),
        wc_customer_id = COALESCE(EXCLUDED.wc_customer_id, customers.wc_customer_id),
        updated_at     = NOW()
    RETURNING id
""")

UPSERT_ORDER = text("""
    INSERT INTO orders (
        so_number, wc_order_id, wc_order_number, wc_status, source_channel,
        customer_id, customer_name, customer_phone,
        delivery_fee, discount_amount, total_receivable,
        order_date, updated_at
    ) VALUES (
        :so_number, :wc_order_id, :wc_order_number, :wc_status, :source_channel,
        :customer_id, :customer_name, :customer_phone,
        :delivery_fee, :discount_amount, :total_receivable,
        :order_date, NOW()
    )
    ON CONFLICT (so_number) DO UPDATE SET
        wc_order_id      = COALESCE(EXCLUDED.wc_order_id,      orders.wc_order_id),
        wc_order_number  = COALESCE(EXCLUDED.wc_order_number,  orders.wc_order_number),
        wc_status        = EXCLUDED.wc_status,
        customer_id      = COALESCE(EXCLUDED.customer_id,      orders.customer_id),
        customer_name    = COALESCE(EXCLUDED.customer_name,    orders.customer_name),
        customer_phone   = COALESCE(EXCLUDED.customer_phone,   orders.customer_phone),
        delivery_fee     = COALESCE(EXCLUDED.delivery_fee,     orders.delivery_fee),
        discount_amount  = COALESCE(EXCLUDED.discount_amount,  orders.discount_amount),
        total_receivable = COALESCE(EXCLUDED.total_receivable, orders.total_receivable),
        updated_at       = NOW()
""")

UPSERT_ITEM = text("""
    INSERT INTO order_items (
        so_number, sku, product_name, size, color,
        quantity, unit_price, total_price, item_discount, price_after_discount
    ) VALUES (
        :so_number, :sku, :product_name, :size, :color,
        :quantity, :unit_price, :total_price, :item_discount, :price_after_discount
    )
    ON CONFLICT (so_number, sku, COALESCE(size,''), COALESCE(color,''))
    DO UPDATE SET
        quantity             = EXCLUDED.quantity,
        unit_price           = EXCLUDED.unit_price,
        total_price          = EXCLUDED.total_price,
        price_after_discount = EXCLUDED.price_after_discount
""")

UPDATE_SKU_IMAGE = text("""
    UPDATE skus SET image_url = :image_url, wc_product_id = :wc_product_id, updated_at = NOW()
    WHERE sku = :sku AND (image_url IS NULL OR image_url != :image_url)
""")


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_orders():
    print(f"\n=== WooCommerce Orders → Brain  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('woocommerce', 'orders')
    since = log.last_record_at()
    modified_after = since.isoformat() if since else None

    if modified_after:
        print(f"  Incremental: modified after {modified_after}")
    else:
        print("  Full sync — no previous sync found")

    ok = err = 0
    newest_dt = since

    with get_connection() as conn:
        for order in wc.iter_orders(modified_after=modified_after):
            try:
                # Customer
                cust = map_customer(order)
                customer_id = None
                if cust:
                    row = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
                    customer_id = row[0] if row else None

                # Order
                o = map_order(order, customer_id)
                conn.execute(UPSERT_ORDER, o)

                # Items
                for item in map_items(order):
                    conn.execute(UPSERT_ITEM, item)

                conn.commit()
                ok += 1

                # Track newest modified date for sync_log
                mod = order.get('date_modified')
                if mod:
                    mod_dt = datetime.fromisoformat(mod[:19])
                    if newest_dt is None or mod_dt > newest_dt:
                        newest_dt = mod_dt

                if ok % 100 == 0:
                    print(f"  ... {ok} orders synced")

            except Exception as e:
                err += 1
                conn.rollback()
                print(f"  ✗ WC-{order.get('id')}: {e}")

    print(f"\n── Summary ── synced:{ok}  errors:{err}\n")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=newest_dt)
    else:
        log.error(f"{err} errors during WC order sync")


def sync_products():
    print(f"\n=== WooCommerce Products → SKU images  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    log = SyncLog('woocommerce', 'products')
    since = log.last_record_at()
    modified_after = since.isoformat() if since else None

    ok = skip = 0

    with get_connection() as conn:
        for p in wc.iter_products(modified_after=modified_after):
            sku = (p.get('sku') or '').strip()
            images = p.get('images') or []
            image_url = images[0].get('src') if images else None

            if sku and image_url:
                conn.execute(UPDATE_SKU_IMAGE, {
                    'sku': sku,
                    'image_url': image_url,
                    'wc_product_id': p.get('id'),
                })
                ok += 1
            else:
                skip += 1

            # Handle variable product variations
            for var in (p.get('variations_detail') or []):
                var_sku = (var.get('sku') or '').strip()
                var_imgs = var.get('images') or []
                var_img = var_imgs[0].get('src') if var_imgs else image_url
                if var_sku and var_img:
                    conn.execute(UPDATE_SKU_IMAGE, {
                        'sku': var_sku,
                        'image_url': var_img,
                        'wc_product_id': p.get('id'),
                    })
                    ok += 1

        conn.commit()

    print(f"  ✓ {ok} SKU images updated  |  {skip} skipped (no SKU or image)\n")
    log.finish(records_synced=ok, last_record_at=datetime.now())


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='WooCommerce → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--orders',   action='store_true', help='Sync orders, customers, line items')
    g.add_argument('--products', action='store_true', help='Sync product image URLs')
    args = ap.parse_args()

    if args.orders:
        sync_orders()
    elif args.products:
        sync_products()
