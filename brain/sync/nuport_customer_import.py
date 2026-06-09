"""
Import Nuport customer export CSV into the Brain.

Adds new columns to the customers table (migration runs automatically),
then upserts all customer records.

Usage:
  python -m sync.nuport_customer_import --file "path/to/customers.csv"

Safe to re-run — uses ON CONFLICT (phone) DO UPDATE.
"""
import re
import sys
import csv
import argparse
from datetime import datetime
import psycopg2.extras
import time

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import engine

BATCH = 200
NOW   = datetime.now()

# New columns to add to customers table (migration is idempotent)
_NEW_COLUMNS = [
    ("nuport_customer_id",    "VARCHAR"),
    ("email",                 "VARCHAR"),
    ("customer_tag",          "VARCHAR"),
    ("customer_source",       "VARCHAR"),
    ("district",              "VARCHAR"),
    ("total_delivered_value", "NUMERIC(12,2)"),
    ("order_frequency",       "INTEGER"),
    ("nuport_created_at",     "TIMESTAMP"),
    ("last_order_date",       "TIMESTAMP"),
    ("pending_orders",        "INTEGER DEFAULT 0"),
    ("on_hold_orders",        "INTEGER DEFAULT 0"),
    ("approved_orders",       "INTEGER DEFAULT 0"),
    ("processing_orders",     "INTEGER DEFAULT 0"),
    ("shipped_orders",        "INTEGER DEFAULT 0"),
    ("in_transit_orders",     "INTEGER DEFAULT 0"),
    ("delivered_orders",      "INTEGER DEFAULT 0"),
    ("flagged_orders",        "INTEGER DEFAULT 0"),
    ("cancelled_orders",      "INTEGER DEFAULT 0"),
]

SQL_UPSERT = """
    INSERT INTO customers (
        phone, name, address,
        nuport_customer_id, email, customer_tag, customer_source, district,
        total_delivered_value, order_frequency,
        nuport_created_at, last_order_date,
        pending_orders, on_hold_orders, approved_orders, processing_orders,
        shipped_orders, in_transit_orders, delivered_orders,
        flagged_orders, cancelled_orders,
        updated_at
    ) VALUES %s
    ON CONFLICT (phone) DO UPDATE SET
        name                  = EXCLUDED.name,
        address               = COALESCE(EXCLUDED.address,            customers.address),
        nuport_customer_id    = COALESCE(EXCLUDED.nuport_customer_id, customers.nuport_customer_id),
        email                 = COALESCE(EXCLUDED.email,              customers.email),
        customer_tag          = EXCLUDED.customer_tag,
        customer_source       = EXCLUDED.customer_source,
        district              = COALESCE(EXCLUDED.district,           customers.district),
        total_delivered_value = EXCLUDED.total_delivered_value,
        order_frequency       = EXCLUDED.order_frequency,
        nuport_created_at     = COALESCE(customers.nuport_created_at, EXCLUDED.nuport_created_at),
        last_order_date       = EXCLUDED.last_order_date,
        pending_orders        = EXCLUDED.pending_orders,
        on_hold_orders        = EXCLUDED.on_hold_orders,
        approved_orders       = EXCLUDED.approved_orders,
        processing_orders     = EXCLUDED.processing_orders,
        shipped_orders        = EXCLUDED.shipped_orders,
        in_transit_orders     = EXCLUDED.in_transit_orders,
        delivered_orders      = EXCLUDED.delivered_orders,
        flagged_orders        = EXCLUDED.flagged_orders,
        cancelled_orders      = EXCLUDED.cancelled_orders,
        updated_at            = EXCLUDED.updated_at
"""


# ── Parsers ───────────────────────────────────────────────────────────────────

def _dt(val):
    if not val or str(val).strip() in ('', '-', 'N/A', 'n/a'):
        return None
    raw = str(val).strip()
    s = raw[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d',
                '%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
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


# ── Migration ─────────────────────────────────────────────────────────────────

def _get_raw():
    for attempt in range(5):
        try:
            return engine.raw_connection()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  ⚠ connect failed ({e}), retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Could not connect to database after 5 attempts")


def run_migration():
    raw = _get_raw()
    cur = raw.cursor()

    # Widen short VARCHAR columns — long addresses/names from CSV exceed VARCHAR(100)
    for col in ('name', 'address', 'phone', 'email',
                'nuport_customer_id', 'customer_tag', 'customer_source', 'district'):
        cur.execute(f"ALTER TABLE customers ALTER COLUMN {col} TYPE TEXT")

    # Add new columns
    added = 0
    for col, typ in _NEW_COLUMNS:
        cur.execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {typ}")
        added += 1

    raw.commit()
    cur.close()
    raw.close()
    print(f"  ✓ Migration: columns widened to TEXT, {added} new columns ensured\n")


# ── CSV load ──────────────────────────────────────────────────────────────────

def load_csv(path: str) -> list:
    rows = []
    skipped = 0
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = _str(row.get('Customer Phone'))
            if not phone:
                skipped += 1
                continue
            rows.append(row)
    print(f"  {len(rows) + skipped} rows  |  {len(rows)} with phone  |  {skipped} skipped (no phone)\n")
    return rows


# ── Build tuples ──────────────────────────────────────────────────────────────

def build_tuples(rows: list) -> list:
    tuples = []
    for row in rows:
        phone = _str(row.get('Customer Phone'))
        if not phone:
            continue
        tuples.append((
            phone,
            _str(row.get('Customer Name')),
            _str(row.get('Location')),
            _str(row.get('Customer ID')),
            _str(row.get('Customer Email')),
            _str(row.get('Customer Tag')),
            _str(row.get('Customer Source')),
            _str(row.get('District')),
            _float(row.get('Total Delivered Order Value')),
            _int(row.get('Order Frequency')),
            _dt(row.get('Created At')),
            _dt(row.get('Last Order Date')),
            _int(row.get('Pending Orders'))    or 0,
            _int(row.get('On Hold Orders'))    or 0,
            _int(row.get('Approved Orders'))   or 0,
            _int(row.get('Processing Orders')) or 0,
            _int(row.get('Shipped Orders'))    or 0,
            _int(row.get('In-Transit Orders')) or 0,
            _int(row.get('Delivered Orders'))  or 0,
            _int(row.get('Flagged Orders'))    or 0,
            _int(row.get('Cancelled Orders'))  or 0,
            NOW,
        ))
    return tuples


# ── Bulk upsert ───────────────────────────────────────────────────────────────

def _bulk(sql, tuples):
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
            print(f"  customers: {ok}/{total} ({ok/total*100:.0f}%)", end='\r')
            time.sleep(0.05)
        except Exception as e:
            print(f"\n  ⚠ batch {i} failed ({e}), reconnecting...")
            try:
                cur.close(); raw.close()
            except Exception:
                pass
            time.sleep(2)
            raw = _get_raw()
            cur = raw.cursor()

    cur.close()
    raw.close()
    print(f"  customers: {ok}/{total} (100%) ✓")
    return ok


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Import Nuport customer CSV into Brain')
    ap.add_argument('--file', required=True, help='Path to the Nuport customer CSV export')
    args = ap.parse_args()

    print(f"\n=== Nuport Customer Import  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    print(f"  File: {args.file}\n")

    print("── Step 1: Migrate customers table ──")
    run_migration()

    print("── Step 2: Load CSV ──")
    rows = load_csv(args.file)

    print("── Step 3: Build tuples ──")
    tuples = build_tuples(rows)
    print(f"  {len(tuples)} records to upsert\n")

    print("── Step 4: Upsert customers ──")
    ok = _bulk(SQL_UPSERT, tuples)

    print(f"\n── Summary ──")
    print(f"  Upserted: {ok} customers")
    print("\n✓ Done\n")
