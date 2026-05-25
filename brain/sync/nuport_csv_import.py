"""
Import Nuport full order export CSV into the Brain.

The CSV has one row per order item. Orders with multiple products appear
as multiple rows with the same Invoice (SO number).

Usage:
  python -m sync.nuport_csv_import --file "C:\\path\\to\\nuport_orders.csv"

Safe to re-run — all upserts use ON CONFLICT DO UPDATE.
Uses psycopg2 execute_values for true bulk inserts (one SQL per batch).
"""
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime
import psycopg2.extras

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import engine

BATCH = 200
NOW   = datetime.now()


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


def _str(val):
    v = (val or '').strip()
    return v or None


def _parse_attr(attr_str):
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


# ── SQL (psycopg2 execute_values style — VALUES %s) ───────────────────────────

SQL_CUSTOMER = """
    INSERT INTO customers (phone, name, address, updated_at)
    VALUES %s
    ON CONFLICT (phone) DO UPDATE SET
        name       = COALESCE(EXCLUDED.name,    customers.name),
        address    = COALESCE(EXCLUDED.address, customers.address),
        updated_at = EXCLUDED.updated_at
"""

SQL_ORDER = """
    INSERT INTO orders (
        so_number, nuport_status, source_channel,
        customer_name, customer_phone, wc_order_number,
        product_total, delivery_fee, discount_amount, total_receivable,
        collected_amount, pathao_waybill,
        order_date, shipped_date, delivered_date, updated_at
    ) VALUES %s
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
        updated_at       = EXCLUDED.updated_at
"""

SQL_ITEM = """
    INSERT INTO order_items (
        so_number, sku, product_name, size, color,
        quantity, unit_price, total_price, item_discount, price_after_discount
    ) VALUES %s
    ON CONFLICT (so_number, sku, COALESCE(size,''), COALESCE(color,''))
    DO UPDATE SET
        quantity             = EXCLUDED.quantity,
        unit_price           = EXCLUDED.unit_price,
        total_price          = EXCLUDED.total_price,
        item_discount        = EXCLUDED.item_discount,
        price_after_discount = EXCLUDED.price_after_discount
"""

SQL_LINK = """
    UPDATE orders o SET customer_id = c.id
    FROM customers c
    WHERE o.customer_phone = c.phone AND o.customer_id IS NULL
"""


# ── CSV load ──────────────────────────────────────────────────────────────────

def load_csv(path: str) -> dict:
    groups = defaultdict(list)
    total_rows = skipped = 0

    with open(path, encoding='utf-8-sig', newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            import csv as _csv
            dialect = _csv.Sniffer().sniff(sample, delimiters=',\t;|')
        except Exception:
            import csv as _csv
            dialect = _csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        print(f"  Detected delimiter: {repr(dialect.delimiter)}")
        for row in reader:
            total_rows += 1
            so = _str(row.get('Invoice'))
            if not so:
                skipped += 1
                continue
            groups[so].append(row)

    print(f"  {total_rows} rows  |  {len(groups)} unique orders  |  {skipped} skipped\n")
    return groups


# ── Build tuples ──────────────────────────────────────────────────────────────

def build_tuples(groups: dict):
    customers = {}
    orders    = []
    items     = []

    for so_number, rows in groups.items():
        first = rows[0]
        phone = _str(first.get('Customer Phone Number'))

        if phone and phone not in customers:
            customers[phone] = (
                phone,
                _str(first.get('Customer Name')),
                _str(first.get('Customer Address')),
                NOW,
            )

        sales  = _float(first.get('Sales Amount'))
        fee    = _float(first.get('Delivery Fee'))
        disc   = _float(first.get('Order Discount'))
        recv   = round(sales + fee - (disc or 0), 2) if sales is not None and fee is not None else None

        orders.append((
            so_number,
            _str(first.get('Status')),
            _str(first.get('Order Source')),
            _str(first.get('Customer Name')),
            phone,
            _str(first.get('Website Order ID')),
            sales, fee, disc, recv,
            _float(first.get('Payments/Paid Amount')),
            _str(first.get('Delivery ID')),
            _dt(first.get('Creation Date')),
            _dt(first.get('Shipped At')),
            _dt(first.get('Delivered At')),
            NOW,
        ))

        seen_items = {}
        for row in rows:
            sku = _str(row.get('Product SKU'))
            if not sku:
                continue
            size, color = _parse_attr(row.get('Product Attributes'))
            key = (so_number, sku, size or '', color or '')
            qty        = _int(row.get('Product Qty')) or 1
            unit_price = _float(row.get('Unit Price'))
            if key in seen_items:
                # duplicate within same order — sum quantities and prices
                prev = seen_items[key]
                prev_qty = prev[5]
                seen_items[key] = (
                    so_number, sku, prev[2], size, color,
                    prev_qty + qty,
                    unit_price,
                    (_float(row.get('Total Price')) or 0) + (prev[7] or 0),
                    (_float(row.get('Product Discount')) or 0) + (prev[8] or 0),
                    (_float(row.get('Price After Discount')) or 0) + (prev[9] or 0),
                )
            else:
                seen_items[key] = (
                    so_number, sku,
                    _str(row.get('Product Name')),
                    size, color,
                    qty,
                    unit_price,
                    _float(row.get('Total Price')),
                    _float(row.get('Product Discount')) or 0,
                    _float(row.get('Price After Discount')),
                )
        items.extend(seen_items.values())

    return list(customers.values()), orders, items


# ── Bulk upsert ───────────────────────────────────────────────────────────────

def _bulk(sql, tuples, label):
    """Upsert in small batches, reconnecting on SSL drop."""
    total = len(tuples)
    ok = 0
    i = 0
    while i < total:
        batch = tuples[i:i + BATCH]
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            psycopg2.extras.execute_values(cur, sql, batch, page_size=BATCH)
            raw.commit()
            cur.close()
            ok += len(batch)
            i += BATCH
            pct = ok / total * 100
            print(f"  {label}: {ok}/{total} ({pct:.0f}%)", end='\r')
        except Exception as e:
            try:
                raw.rollback()
            except Exception:
                pass
            print(f"\n  ⚠ batch {i}-{i+len(batch)} failed ({e}), retrying...")
        finally:
            try:
                raw.close()
            except Exception:
                pass
    print(f"  {label}: {ok}/{total} (100%) ✓")
    return ok


def process(groups: dict):
    print("Building records in memory...")
    customers, orders, items = build_tuples(groups)
    print(f"  {len(customers)} customers | {len(orders)} orders | {len(items)} items\n")

    print("Upserting customers...")
    c_ok = _bulk(SQL_CUSTOMER, customers, 'customers')

    print("Upserting orders...")
    o_ok = _bulk(SQL_ORDER, orders, 'orders')

    print("Upserting order items...")
    i_ok = _bulk(SQL_ITEM, items, 'items')

    print("Linking customer IDs to orders...")
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(SQL_LINK)
        raw.commit()
        cur.close()
    finally:
        raw.close()
    print("  ✓ done\n")

    print("── Summary ──")
    print(f"  customers : {c_ok}")
    print(f"  orders    : {o_ok}")
    print(f"  items     : {i_ok}")


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
