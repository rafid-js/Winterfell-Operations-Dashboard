"""
Import Nuport full order export CSV into the Brain.

The CSV has one row per order item. Orders with multiple products appear
as multiple rows with the same Invoice (SO number).

Usage:
  python -m sync.nuport_csv_import --file "C:\\path\\to\\nuport_orders.csv"

Safe to re-run — all upserts use ON CONFLICT DO UPDATE.
Uses psycopg2 execute_values for true bulk inserts (one SQL per batch).
"""
import re
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime
import psycopg2.extras
import time

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import engine

BATCH = 200
NOW   = datetime.now()


# ── Parsers ───────────────────────────────────────────────────────────────────

def _dt(val):
    if not val or str(val).strip() in ('', '-', 'N/A', 'n/a'):
        return None
    raw = str(val).strip()

    # Standard compact formats (ISO, DD/MM/YYYY, MM/DD/YYYY)
    s = raw[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d',
                '%d/%m/%Y %H:%M:%S', '%d/%m/%Y',
                '%m/%d/%Y %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    # Nuport CSV format: "March 25th 2025, 5:08:09 pm" — strip ordinal suffix first
    clean = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', raw)
    for fmt in ('%B %d %Y, %I:%M:%S %p', '%B %d %Y, %I:%M %p', '%B %d %Y'):
        try:
            return datetime.strptime(clean, fmt)
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
    ON CONFLICT (phone) DO NOTHING
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
        order_date     = COALESCE(orders.order_date,     EXCLUDED.order_date),
        shipped_date   = COALESCE(orders.shipped_date,   EXCLUDED.shipped_date),
        delivered_date = COALESCE(orders.delivered_date, EXCLUDED.delivered_date)
"""

SQL_ITEM = """
    INSERT INTO order_items (
        so_number, sku, product_name, size, color,
        quantity, unit_price, total_price, item_discount, price_after_discount
    ) VALUES %s
    ON CONFLICT (so_number, sku, COALESCE(size,''), COALESCE(color,''))
    DO NOTHING
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
                # Use product name as fallback so qty is still counted
                fallback = _str(row.get('Product Name')) or 'UNKNOWN'
                sku = '~' + fallback[:80]  # ~ prefix marks placeholder SKUs
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

def _get_raw():
    for attempt in range(5):
        try:
            return engine.raw_connection()
        except Exception as e:
            wait = 2 ** attempt
            print(f"\n  ⚠ connect failed ({e}), retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Could not connect to database after 5 attempts")


def _bulk(sql, tuples, label):
    """Upsert in batches, reusing one connection and reconnecting only on failure."""
    total = len(tuples)
    ok = 0
    i = 0
    raw = _get_raw()
    cur = raw.cursor()

    while i < total:
        batch = tuples[i:i + BATCH]
        try:
            psycopg2.extras.execute_values(cur, sql, batch, page_size=BATCH)
            raw.commit()
            ok += len(batch)
            i += BATCH
            pct = ok / total * 100
            print(f"  {label}: {ok}/{total} ({pct:.0f}%)", end='\r')
            time.sleep(0.05)  # small pause — don't overwhelm Railway
        except Exception as e:
            print(f"\n  ⚠ batch {i} failed ({e}), reconnecting...")
            try:
                cur.close()
                raw.close()
            except Exception:
                pass
            time.sleep(2)
            raw = _get_raw()
            cur = raw.cursor()
            # retry same batch — don't advance i

    cur.close()
    raw.close()
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
    raw = _get_raw()
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
