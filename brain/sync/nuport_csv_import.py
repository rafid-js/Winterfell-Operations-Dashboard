"""
Import Nuport full order export CSV into the Brain.

The CSV has one row per order item. Orders with multiple products appear
as multiple rows with the same Invoice (SO number).

Usage:
  python -m sync.nuport_csv_import --file "C:\\path\\to\\nuport_orders.csv"

Safe to re-run — all upserts use ON CONFLICT DO UPDATE / DO NOTHING.
"""
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import get_connection


# ── Parsers ───────────────────────────────────────────────────────────────────

def _dt(val):
    if not val or str(val).strip() in ('', '-', 'N/A', 'n/a'):
        return None
    s = str(val).strip()[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d',
                '%d/%m/%Y %H:%M:%S', '%d/%m/%Y',
                '%m/%d/%Y %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _float(val):
    try:
        return float(str(val).replace(',', '').strip()) if val and str(val).strip() else None
    except (ValueError, TypeError):
        return None


def _int(val):
    try:
        v = _float(val)
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _parse_attr(attr_str):
    """Parse 'sizes: L' or 'color: Red' → (size, color)."""
    size = color = None
    if not attr_str or not str(attr_str).strip():
        return size, color
    parts = str(attr_str).split(':', 1)
    if len(parts) != 2:
        return size, color
    key = parts[0].strip().lower()
    val = parts[1].strip()
    if not val:
        return size, color
    if 'size' in key:
        size = val
    elif 'color' in key or 'colour' in key:
        color = val
    return size, color


# ── SQL ───────────────────────────────────────────────────────────────────────

UPSERT_CUSTOMER = text("""
    INSERT INTO customers (phone, name, address, updated_at)
    VALUES (:phone, :name, :address, NOW())
    ON CONFLICT (phone) DO UPDATE SET
        name       = COALESCE(EXCLUDED.name,    customers.name),
        address    = COALESCE(EXCLUDED.address, customers.address),
        updated_at = NOW()
    RETURNING id
""")

UPSERT_ORDER = text("""
    INSERT INTO orders (
        so_number, nuport_status, source_channel,
        customer_id, customer_name, customer_phone,
        wc_order_number,
        product_total, delivery_fee, discount_amount, total_receivable,
        collected_amount,
        pathao_waybill,
        order_date, shipped_date, delivered_date,
        updated_at
    ) VALUES (
        :so_number, :nuport_status, :source_channel,
        :customer_id, :customer_name, :customer_phone,
        :wc_order_number,
        :product_total, :delivery_fee, :discount_amount, :total_receivable,
        :collected_amount,
        :pathao_waybill,
        :order_date, :shipped_date, :delivered_date,
        NOW()
    )
    ON CONFLICT (so_number) DO UPDATE SET
        nuport_status    = COALESCE(EXCLUDED.nuport_status,    orders.nuport_status),
        source_channel   = COALESCE(EXCLUDED.source_channel,   orders.source_channel),
        customer_id      = COALESCE(EXCLUDED.customer_id,      orders.customer_id),
        customer_name    = COALESCE(EXCLUDED.customer_name,    orders.customer_name),
        customer_phone   = COALESCE(EXCLUDED.customer_phone,   orders.customer_phone),
        wc_order_number  = COALESCE(EXCLUDED.wc_order_number,  orders.wc_order_number),
        product_total    = COALESCE(EXCLUDED.product_total,    orders.product_total),
        delivery_fee     = COALESCE(EXCLUDED.delivery_fee,     orders.delivery_fee),
        discount_amount  = COALESCE(EXCLUDED.discount_amount,  orders.discount_amount),
        total_receivable = COALESCE(EXCLUDED.total_receivable, orders.total_receivable),
        collected_amount = COALESCE(EXCLUDED.collected_amount, orders.collected_amount),
        pathao_waybill   = COALESCE(EXCLUDED.pathao_waybill,   orders.pathao_waybill),
        shipped_date     = COALESCE(EXCLUDED.shipped_date,     orders.shipped_date),
        delivered_date   = COALESCE(EXCLUDED.delivered_date,   orders.delivered_date),
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
        item_discount        = EXCLUDED.item_discount,
        price_after_discount = EXCLUDED.price_after_discount
""")


# ── Import ────────────────────────────────────────────────────────────────────

def load_csv(path: str) -> dict:
    """Read CSV and group rows by Invoice (SO number)."""
    groups = defaultdict(list)
    total_rows = 0
    skipped = 0

    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            total_rows += 1
            so = (row.get('Invoice') or '').strip()
            if not so:
                skipped += 1
                continue
            groups[so].append(row)

    print(f"  {total_rows} rows read  |  {len(groups)} unique orders  |  {skipped} rows skipped (no Invoice)")
    return groups


def process(groups: dict):
    orders_ok = orders_err = 0
    items_ok = items_err = 0
    customers_ok = 0

    with get_connection() as conn:
        for idx, (so_number, rows) in enumerate(groups.items(), 1):
            first = rows[0]

            # ── Customer ──────────────────────────────────────────
            phone = (first.get('Customer Phone Number') or '').strip()
            customer_id = None
            if phone:
                try:
                    cust = {
                        'phone':   phone,
                        'name':    (first.get('Customer Name') or '').strip() or None,
                        'address': (first.get('Customer Address') or '').strip() or None,
                    }
                    row = conn.execute(UPSERT_CUSTOMER, cust).fetchone()
                    customer_id = row[0] if row else None
                    customers_ok += 1
                except Exception as e:
                    print(f"  ⚠ Customer {phone}: {e}")

            # ── Order ─────────────────────────────────────────────
            sales_amount  = _float(first.get('Sales Amount'))
            delivery_fee  = _float(first.get('Delivery Fee'))
            order_discount = _float(first.get('Order Discount'))
            paid_amount   = _float(first.get('Payments/Paid Amount'))
            wc_raw        = (first.get('Website Order ID') or '').strip() or None

            total_recv = None
            if sales_amount is not None and delivery_fee is not None:
                total_recv = round(
                    sales_amount + delivery_fee - (order_discount or 0), 2
                )

            order = {
                'so_number':        so_number,
                'nuport_status':    (first.get('Status') or '').strip() or None,
                'source_channel':   (first.get('Order Source') or '').strip() or None,
                'customer_id':      customer_id,
                'customer_name':    (first.get('Customer Name') or '').strip() or None,
                'customer_phone':   phone or None,
                'wc_order_number':  wc_raw,
                'product_total':    sales_amount,
                'delivery_fee':     delivery_fee,
                'discount_amount':  order_discount,
                'total_receivable': total_recv,
                'collected_amount': paid_amount,
                'pathao_waybill':   (first.get('Delivery ID') or '').strip() or None,
                'order_date':       _dt(first.get('Creation Date')),
                'shipped_date':     _dt(first.get('Shipped At')),
                'delivered_date':   _dt(first.get('Delivered At')),
            }

            try:
                conn.execute(UPSERT_ORDER, order)
                orders_ok += 1
            except Exception as e:
                orders_err += 1
                print(f"  ✗ Order {so_number}: {e}")
                conn.rollback()
                continue

            # ── Order items ───────────────────────────────────────
            for row in rows:
                sku = (row.get('Product SKU') or '').strip()
                if not sku:
                    continue
                size, color = _parse_attr(row.get('Product Attributes'))
                item = {
                    'so_number':            so_number,
                    'sku':                  sku,
                    'product_name':         (row.get('Product Name') or '').strip() or None,
                    'size':                 size,
                    'color':                color,
                    'quantity':             _int(row.get('Product Qty')) or 1,
                    'unit_price':           _float(row.get('Unit Price')),
                    'total_price':          _float(row.get('Total Price')),
                    'item_discount':        _float(row.get('Product Discount')) or 0,
                    'price_after_discount': _float(row.get('Price After Discount')),
                }
                try:
                    conn.execute(UPSERT_ITEM, item)
                    items_ok += 1
                except Exception as e:
                    items_err += 1
                    print(f"  ✗ Item {so_number}/{sku}: {e}")

            conn.commit()

            if idx % 1000 == 0:
                pct = idx / len(groups) * 100
                print(f"  ... {idx}/{len(groups)} orders ({pct:.0f}%)")

    print(f"\n── Summary ──")
    print(f"  customers upserted : {customers_ok}")
    print(f"  orders    upserted : {orders_ok}   errors: {orders_err}")
    print(f"  items     upserted : {items_ok}   errors: {items_err}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Import Nuport CSV export into Brain')
    ap.add_argument('--file', required=True, help='Path to the Nuport CSV export file')
    args = ap.parse_args()

    print(f"\n=== Nuport CSV Import  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    print(f"  File: {args.file}\n")

    groups = load_csv(args.file)
    process(groups)
    print("\n✓ Done\n")
