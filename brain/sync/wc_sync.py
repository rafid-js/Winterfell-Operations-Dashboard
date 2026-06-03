"""
WooCommerce → Brain sync.

Modes:
  python -m sync.wc_sync --orders       sync orders + line items + customers
  python -m sync.wc_sync --products     sync product image URLs → skus table
  python -m sync.wc_sync --reconcile    merge WC-XXXX duplicates into Nuport orders (run once)

Incremental by default — reads last_record_at from sync_log.
Universal keys: phone (customers), WC-{id} or matched so_number (orders), sku (products).

Reconciliation: for each WC order, if the Brain already has an order with matching
wc_order_id or wc_order_number (from Nuport), we update that row instead of creating
a duplicate WC-XXXX row.
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


def map_items(order: dict, so_number: str | None = None) -> list[dict]:
    items = []
    so = so_number or f"WC-{order.get('id')}"
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

UPDATE_WC_FIELDS = text("""
    UPDATE orders SET
        wc_order_id     = :wc_id,
        wc_order_number = COALESCE(wc_order_number, :wc_num),
        wc_status       = :wc_status,
        source_channel  = COALESCE(source_channel, 'woocommerce'),
        updated_at      = NOW()
    WHERE so_number = :so_number
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

    # Pre-load all known wc_order_numbers from Nuport orders into memory.
    # This replaces one-per-order SQL lookups with O(1) dict gets.
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT so_number, wc_order_id, wc_order_number
            FROM orders
            WHERE so_number NOT LIKE 'WC-%'
              AND (wc_order_id IS NOT NULL OR wc_order_number IS NOT NULL)
        """)).mappings().all()

    nuport_by_wc_id  = {}   # int wc_order_id  → so_number
    nuport_by_wc_num = {}   # str wc_order_number → so_number
    for r in rows:
        if r['wc_order_id']:
            nuport_by_wc_id[r['wc_order_id']] = r['so_number']
        if r['wc_order_number']:
            nuport_by_wc_num[str(r['wc_order_number'])] = r['so_number']

    print(f"  Nuport lookup loaded: {len(nuport_by_wc_num)} orders with wc_order_number\n")

    ok = err = merged = 0
    newest_dt = since
    BATCH_SIZE = 50

    with get_connection() as conn:
        for order in wc.iter_orders(modified_after=modified_after):
            try:
                wc_id  = order.get('id')
                wc_num = str(order.get('number') or wc_id)

                # Customer
                cust = map_customer(order)
                customer_id = None
                if cust:
                    row = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
                    customer_id = row[0] if row else None

                # In-memory lookup — no extra SQL per order
                so_number = nuport_by_wc_id.get(wc_id) or nuport_by_wc_num.get(wc_num)

                if so_number:
                    conn.execute(UPDATE_WC_FIELDS, {
                        'wc_id': wc_id, 'wc_num': wc_num,
                        'wc_status': _str(order.get('status')),
                        'so_number': so_number,
                    })
                    merged += 1
                else:
                    so_number = f'WC-{wc_id}'
                    o = map_order(order, customer_id)
                    conn.execute(UPSERT_ORDER, o)

                for item in map_items(order, so_number):
                    conn.execute(UPSERT_ITEM, item)

                ok += 1

                mod = order.get('date_modified')
                if mod:
                    mod_dt = datetime.fromisoformat(mod[:19])
                    if newest_dt is None or mod_dt > newest_dt:
                        newest_dt = mod_dt

                if ok % BATCH_SIZE == 0:
                    conn.commit()
                if ok % 200 == 0:
                    print(f"  ... {ok} orders processed ({merged} merged, {ok-merged} WC-only)")

            except Exception as e:
                err += 1
                conn.rollback()
                print(f"  ✗ WC-{order.get('id')}: {e}")

        conn.commit()

    print(f"\n── Summary ── total:{ok}  merged:{merged}  WC-only:{ok-merged-err}  errors:{err}")
    print(f"  Merge rate: {round(merged/ok*100)}%" if ok else "")
    if err == 0:
        log.finish(records_synced=ok, last_record_at=newest_dt)
    else:
        log.error(f"{err} errors during WC order sync")

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


def reconcile_duplicates():
    """
    One-time cleanup: find WC-XXXX rows that duplicate a Nuport order,
    move their items to the Nuport so_number, then delete the WC-XXXX row.
    Safe to re-run.
    """
    print(f"\n=== WC Reconciliation — merge WC-XXXX duplicates  {datetime.now():%Y-%m-%d %H:%M} ===\n")

    with get_connection() as conn:
        # Find WC-XXXX rows that have a matching Nuport order
        pairs = conn.execute(text("""
            SELECT wc.so_number AS wc_so, np.so_number AS np_so
            FROM orders wc
            JOIN orders np ON (
                (wc.wc_order_id IS NOT NULL AND wc.wc_order_id = np.wc_order_id)
                OR
                (wc.wc_order_number IS NOT NULL AND wc.wc_order_number = np.wc_order_number)
            )
            WHERE wc.so_number LIKE 'WC-%'
              AND np.so_number NOT LIKE 'WC-%'
        """)).mappings().all()

        print(f"  Found {len(pairs)} WC-XXXX duplicates to merge\n")
        merged = 0

        for pair in pairs:
            wc_so = pair['wc_so']
            np_so = pair['np_so']

            # Stamp WC fields onto Nuport order
            conn.execute(text("""
                UPDATE orders np_o SET
                    wc_order_id     = wc_o.wc_order_id,
                    wc_order_number = COALESCE(np_o.wc_order_number, wc_o.wc_order_number),
                    wc_status       = wc_o.wc_status,
                    updated_at      = NOW()
                FROM orders wc_o
                WHERE np_o.so_number = :np_so AND wc_o.so_number = :wc_so
            """), {'np_so': np_so, 'wc_so': wc_so})

            # Move items from WC-XXXX → Nuport so_number (skip conflicts)
            conn.execute(text("""
                INSERT INTO order_items (so_number, sku, product_name, size, color,
                    quantity, unit_price, total_price, item_discount, price_after_discount)
                SELECT :np_so, sku, product_name, size, color,
                    quantity, unit_price, total_price, item_discount, price_after_discount
                FROM order_items
                WHERE so_number = :wc_so
                ON CONFLICT (so_number, sku, COALESCE(size,''), COALESCE(color,''))
                DO NOTHING
            """), {'np_so': np_so, 'wc_so': wc_so})

            # Delete WC-XXXX items and order
            conn.execute(text("DELETE FROM order_items WHERE so_number = :s"), {'s': wc_so})
            conn.execute(text("DELETE FROM orders WHERE so_number = :s"), {'s': wc_so})
            conn.commit()

            print(f"  ✓ {wc_so} → {np_so}")
            merged += 1

    print(f"\n  ✓ Merged {merged} duplicate orders\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='WooCommerce → Brain sync')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--orders',    action='store_true', help='Sync orders, customers, line items')
    g.add_argument('--products',  action='store_true', help='Sync product image URLs')
    g.add_argument('--reconcile', action='store_true', help='Merge WC-XXXX duplicates into Nuport orders')
    args = ap.parse_args()

    if args.orders:
        sync_orders()
    elif args.products:
        sync_products()
    elif args.reconcile:
        reconcile_duplicates()
