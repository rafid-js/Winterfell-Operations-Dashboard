"""
Import Nuport full order export CSV into the Brain.

The CSV has one row per order item. Orders with multiple products appear
as multiple rows with the same Invoice (SO number).

Usage:
  python -m sync.nuport_csv_import --file "C:\\path\\to\\nuport_orders.csv"

Safe to re-run — all upserts use ON CONFLICT DO UPDATE / DO NOTHING.
Batches all inserts for speed (3 round trips total, not 50k+).
"""
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import get_connection

BATCH = 500


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
""")

UPSERT_ORDER = text("""
    INSERT INTO orders (
        so_number, nuport_status, source_channel,
        customer_name, customer_phone,
        wc_order_number,
        product_total, delivery_fee, discount_amount, total_receivable,
        collected_amount,
        pathao_waybill,
        order_date, shipped_date, delivered_date,
        updated_at
    ) VALUES (
        :so_number, :nuport_status, :source_channel,
        :customer_name, :customer_phone,
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

LINK_CUSTOMER_IDS = text("""
    UPDATE orders o
    SET customer_id = c.id
    FROM customers c
    WHERE o.customer_phone = c.phone
      AND o.customer_id IS NULL
""")


# ── CSV load ──────────────────────────────────────────────────────────────────

def load_csv(path: str) -> dict:
    """Read CSV and group rows by Invoice (SO number)."""
    groups = defaultdict(list)
    total_rows = 0
    skipped = 0

    with open(path, encoding='utf-8-sig', newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t;|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        print(f"  Detected delimiter: {repr(dialect.delimiter)}")
        for row in reader:
            total_rows += 1
            so = (row.get('Invoice') or '').strip()
            if not so:
                skipped += 1
                continue
            groups[so].append(row)

    print(f"  {total_rows} rows  |  {len(groups)} unique orders  |  {skipped} skipped (no Invoice)\n")
    return groups


# ── Build records ─────────────────────────────────────────────────────────────

def build_records(groups: dict):
    customers = {}   # phone → dict (deduplicated)
    orders    = []
    items     = []

    for so_number, rows in groups.items():
        first = rows[0]
        phone = (first.get('Customer Phone Number') or '').strip() or None

        if phone and phone not in customers:
            customers[phone] = {
                'phone':   phone,
                'name':    (first.get('Customer Name') or '').strip() or None,
                'address': (first.get('Customer Address') or '').strip() or None,
            }

        sales_amount   = _float(first.get('Sales Amount'))
        delivery_fee   = _float(first.get('Delivery Fee'))
        order_discount = _float(first.get('Order Discount'))
        total_recv = None
        if sales_amount is not None and delivery_fee is not None:
            total_recv = round(sales_amount + delivery_fee - (order_discount or 0), 2)

        orders.append({
            'so_number':        so_number,
            'nuport_status':    (first.get('Status') or '').strip() or None,
            'source_channel':   (first.get('Order Source') or '').strip() or None,
            'customer_name':    (first.get('Customer Name') or '').strip() or None,
            'customer_phone':   phone,
            'wc_order_number':  (first.get('Website Order ID') or '').strip() or None,
            'product_total':    sales_amount,
            'delivery_fee':     delivery_fee,
            'discount_amount':  order_discount,
            'total_receivable': total_recv,
            'collected_amount': _float(first.get('Payments/Paid Amount')),
            'pathao_waybill':   (first.get('Delivery ID') or '').strip() or None,
            'order_date':       _dt(first.get('Creation Date')),
            'shipped_date':     _dt(first.get('Shipped At')),
            'delivered_date':   _dt(first.get('Delivered At')),
        })

        for row in rows:
            sku = (row.get('Product SKU') or '').strip()
            if not sku:
                continue
            size, color = _parse_attr(row.get('Product Attributes'))
            items.append({
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
            })

    return list(customers.values()), orders, items


# ── Batch upsert ──────────────────────────────────────────────────────────────

def _run_batches(conn, sql, records, label):
    total = len(records)
    ok = err = 0
    for i in range(0, total, BATCH):
        batch = records[i:i + BATCH]
        try:
            conn.execute(sql, batch)
            conn.commit()
            ok += len(batch)
        except Exception as e:
            conn.rollback()
            err += len(batch)
            print(f"  ✗ {label} batch {i}-{i+len(batch)}: {e}")
        pct = min(i + BATCH, total) / total * 100
        print(f"  {label}: {min(i+BATCH, total)}/{total} ({pct:.0f}%)", end='\r')
    print()
    return ok, err


def process(groups: dict):
    print("Building records in memory...")
    customers, orders, items = build_records(groups)
    print(f"  {len(customers)} customers  |  {len(orders)} orders  |  {len(items)} items\n")

    with get_connection() as conn:
        print("Upserting customers...")
        c_ok, c_err = _run_batches(conn, UPSERT_CUSTOMER, customers, 'customers')

        print("Upserting orders...")
        o_ok, o_err = _run_batches(conn, UPSERT_ORDER, orders, 'orders')

        print("Upserting order items...")
        i_ok, i_err = _run_batches(conn, UPSERT_ITEM, items, 'items')

        print("Linking customer IDs to orders...")
        conn.execute(LINK_CUSTOMER_IDS)
        conn.commit()
        print("  ✓ done\n")

    print("── Summary ──")
    print(f"  customers : {c_ok} ok  {c_err} errors")
    print(f"  orders    : {o_ok} ok  {o_err} errors")
    print(f"  items     : {i_ok} ok  {i_err} errors")


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
