"""
Supply Chain — data access layer (Phase 1).

All functions return plain Python dicts / lists of dicts (never SQLAlchemy Row
objects) and convert Decimal -> float and date/datetime -> ISO strings so the
results are JSON-serialisable straight out of the route handlers.

Queries use parameterised text() — never string-format user input into SQL.
"""
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'brain'))
from db import get_connection  # noqa: E402


STAGES = ['PO Issued', 'Fabric', 'Trims', 'Sewing', 'QC', 'Delivered']
STAGE_PCT = {
    'PO Issued': 0,
    'Fabric': 17,
    'Trims': 33,
    'Sewing': 50,
    'QC': 83,
    'Delivered': 100,
}

# Canonical apparel size ordering for the PO size matrix.
SIZE_ORDER = {
    'XXS': 0, '2XS': 0, 'XS': 1, 'S': 2, 'M': 3, 'L': 4, 'XL': 5,
    'XXL': 6, '2XL': 6, 'XXXL': 7, '3XL': 7, '4XL': 8, '5XL': 9,
    'FREE': 10, 'OS': 10, 'ONE SIZE': 10,
}

# Default manufacturing lead time (days) used by the Brain quantity recommender.
# Overridable per supplier via suppliers.avg_lead_days, or per request.
#
# Target-stock model: we size production so that the goods cover sales during
# the production wait AND leave a forward runway of stock once they arrive.
#   coverage_days = lead_time + TARGET_RUNWAY_DAYS
# e.g. 15-day production + 10-day runway = 25 days of demand covered, so the
# day the batch lands you still hold ~10 days of forward stock.
DEFAULT_LEAD_TIME_DAYS = 15   # typical production time
TARGET_RUNWAY_DAYS = 10       # forward stock to hold AFTER the goods arrive
MIN_PER_SIZE = 5              # supplier MOQ floor per size (only when the size sells)

# Same regex used by the Products module to strip size suffixes and group SKUs
# into a single "product" (e.g. "Classic Tee - M" → "Classic Tee").
_SIZE_RE_SQL = (
    r'\s*-\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[2-5][0-9])'
    r'(\s*\([^)]*\))?\s*$'
)
_SIZE_RE_PY = re.compile(_SIZE_RE_SQL, re.IGNORECASE)


def _size_sort_key(size):
    s = (size or '').strip().upper()
    # Numeric waist/length sizes (28, 30, 32 …) sort numerically after letter sizes.
    if s.isdigit():
        return (100, int(s), s)
    return (SIZE_ORDER.get(s, 50), 0, s)


def _extract_size_label(product_name, raw_size):
    """Return the size label for one SKU variant."""
    if raw_size and raw_size.strip():
        return raw_size.strip()
    m = _SIZE_RE_PY.search(product_name or '')
    if m:
        return m.group(1)
    return (product_name or '—')



# ── serialisation helpers ───────────────────────────────────────────────────

def _conv(value):
    """Convert a single DB value into a JSON-safe Python value."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _row_to_dict(row):
    """Turn a SQLAlchemy Row into a plain, JSON-safe dict."""
    mapping = row._mapping
    return {key: _conv(mapping[key]) for key in mapping.keys()}


def _rows_to_list(rows):
    return [_row_to_dict(r) for r in rows]


def _has_column(conn, table, column):
    """Return True if the given column exists (used for optional columns)."""
    row = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        LIMIT 1
    """), {'t': table, 'c': column}).fetchone()
    return row is not None


# ── id generators ───────────────────────────────────────────────────────────

def next_po_id(conn):
    """Generate the next PO-XXXX id by finding the current MAX numeric suffix."""
    row = conn.execute(text("""
        SELECT COALESCE(MAX(CAST(SUBSTRING(po_id FROM 4) AS INTEGER)), 0) AS mx
        FROM purchase_orders
        WHERE po_id ~ '^PO-[0-9]+$'
    """)).fetchone()
    nxt = (row[0] if row and row[0] is not None else 0) + 1
    return 'PO-' + str(nxt).zfill(4)


def next_tl_id(conn):
    """Generate the next TL-XXXX id by finding the current MAX numeric suffix."""
    row = conn.execute(text("""
        SELECT COALESCE(MAX(CAST(SUBSTRING(event_id FROM 4) AS INTEGER)), 0) AS mx
        FROM po_timeline
        WHERE event_id ~ '^TL-[0-9]+$'
    """)).fetchone()
    nxt = (row[0] if row and row[0] is not None else 0) + 1
    return 'TL-' + str(nxt).zfill(4)


# ── reads ───────────────────────────────────────────────────────────────────

def get_po_list(status=None):
    """
    Query the active_po_summary view, optionally filtered by status pill
    ('delayed', 'at_risk', 'active'), and return both the list and summary
    counts.
    """
    with get_connection() as conn:
        rows = conn.execute(text("SELECT * FROM active_po_summary")).fetchall()
    pos = _rows_to_list(rows)

    # Compute summary across the full (unfiltered) active set.
    total = len(pos)
    delayed = sum(1 for p in pos if p.get('po_status') == 'Delayed')
    at_risk = sum(1 for p in pos if p.get('po_status') == 'At Risk')
    on_track = total - delayed - at_risk
    units_in_production = sum((p.get('quantity_ordered') or 0) for p in pos)
    capital_deployed = sum((p.get('total_cost_bdt') or 0) for p in pos)

    summary = {
        'total': total,
        'on_track': on_track,
        'at_risk': at_risk,
        'delayed': delayed,
        'units_in_production': units_in_production,
        'capital_deployed': capital_deployed,
    }

    if status:
        status = status.lower()
        if status == 'delayed':
            pos = [p for p in pos if p.get('po_status') == 'Delayed']
        elif status == 'at_risk':
            pos = [p for p in pos if p.get('po_status') == 'At Risk']
        elif status == 'active':
            pos = [p for p in pos
                   if p.get('po_status') not in ('Delayed', 'At Risk')]
        # 'all' or anything else -> no filter

    return {'pos': pos, 'summary': summary}


def get_po_detail(po_id):
    """Return a full PO dict joined with supplier info, or None if not found."""
    with get_connection() as conn:
        row = conn.execute(text("""
            SELECT po.*,
                   s.name           AS supplier_name,
                   s.phone          AS supplier_phone,
                   s.whatsapp        AS supplier_whatsapp,
                   s.location        AS supplier_location,
                   s.reliability_score AS supplier_score
            FROM purchase_orders po
            LEFT JOIN suppliers s ON po.supplier_id = s.id
            WHERE po.po_id = :po_id
        """), {'po_id': po_id}).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_po_timeline(po_id):
    """Return all timeline events for a PO, ordered by event_date ASC."""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM po_timeline
            WHERE po_id = :po_id
            ORDER BY event_date ASC, id ASC
        """), {'po_id': po_id}).fetchall()
    return _rows_to_list(rows)


def get_suppliers():
    """Return all suppliers ordered by name."""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM suppliers ORDER BY name ASC
        """)).fetchall()
    return _rows_to_list(rows)


# ── writes ──────────────────────────────────────────────────────────────────

def create_supplier(data):
    """Insert a new supplier (idempotent by name) and return its id."""
    name = (data.get('name') or '').strip()
    if not name:
        raise ValueError('Supplier name is required')
    with get_connection() as conn:
        sid = _upsert_supplier(conn, data)
        conn.commit()
    return sid


def _upsert_supplier(conn, data):
    """
    Insert the supplier if its name is new, otherwise return the existing id.
    Caller is responsible for committing.
    """
    name = (data.get('name') or '').strip()
    if not name:
        raise ValueError('Supplier name is required')

    existing = conn.execute(text("""
        SELECT id FROM suppliers WHERE name = :name
    """), {'name': name}).fetchone()
    if existing:
        return existing[0]

    row = conn.execute(text("""
        INSERT INTO suppliers (name, phone, whatsapp, email, location, notes)
        VALUES (:name, :phone, :whatsapp, :email, :location, :notes)
        RETURNING id
    """), {
        'name': name,
        'phone': data.get('phone'),
        'whatsapp': data.get('whatsapp'),
        'email': data.get('email'),
        'location': data.get('location'),
        'notes': data.get('notes'),
    }).fetchone()
    return row[0]


def _normalize_po_products(data):
    """
    Return a normalised list of product line items for a PO.

    Accepts either the new multi-product shape (data['products'] = [...]) or the
    legacy single-product shape (top-level product_name / quantity_ordered /
    size_breakdown). Each returned item is:

        {product_name, sku, unit_cost_bdt, quantity, total_cost_bdt, size_breakdown}
    """
    raw = data.get('products')
    if not raw:
        # Legacy single-product fallback.
        raw = [{
            'product_name': data.get('product_name'),
            'sku': data.get('sku'),
            'unit_cost_bdt': data.get('unit_cost_bdt'),
            'quantity': data.get('quantity_ordered') or data.get('quantity'),
            'size_breakdown': data.get('size_breakdown'),
        }]
    if not isinstance(raw, list):
        raise ValueError('products must be a list')

    items = []
    for idx, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError('Each product must be an object')
        name = (p.get('product_name') or '').strip()
        if not name:
            raise ValueError('Product name is required for every line item')

        try:
            qty = int(p.get('quantity') or p.get('quantity_ordered') or 0)
        except (TypeError, ValueError):
            raise ValueError('Quantity must be a number for "' + name + '"')
        if qty <= 0:
            raise ValueError('Quantity must be greater than zero for "' + name + '"')

        uc = p.get('unit_cost_bdt')
        uc = float(uc) if uc not in (None, '') else 0.0

        sku = (p.get('sku') or '').strip() or None

        sb = p.get('size_breakdown')
        if sb is not None and not isinstance(sb, (list, dict)):
            sb = None

        items.append({
            'product_name': name,
            'sku': sku,
            'unit_cost_bdt': uc,
            'quantity': qty,
            'total_cost_bdt': round(uc * qty, 2),
            'size_breakdown': sb,
        })
    return items


def create_po(data):
    """
    Create a purchase order plus its initial 'PO generated' timeline event.

    Supports multiple products per PO via data['products'] (a list of line
    items). The scalar columns are populated with roll-up summaries so the
    existing list/detail/supplier views keep working:
        product_name      -> first product (+ "N more" suffix when multiple)
        sku               -> first product's sku
        quantity_ordered  -> sum of all line quantities
        total_cost_bdt    -> sum of all line totals
        unit_cost_bdt     -> weighted average across lines

    Supplier may be passed as supplier_id or supplier_name (upsert by name).
    Returns the generated po_id.
    """
    products = _normalize_po_products(data)

    due_date = data.get('due_date')
    if not due_date:
        raise ValueError('Due date is required')

    advance_paid = data.get('advance_paid_bdt')
    advance_paid = float(advance_paid) if advance_paid not in (None, '') else 0.0

    # Roll-up summaries across all line items.
    total_qty = sum(p['quantity'] for p in products)
    total_cost = round(sum(p['total_cost_bdt'] for p in products), 2)
    unit_cost = round(total_cost / total_qty, 2) if total_qty else 0.0
    balance_due = total_cost - advance_paid

    first = products[0]
    if len(products) == 1:
        product_name = first['product_name']
    else:
        product_name = first['product_name'] + ' + ' + str(len(products) - 1) + ' more'
    sku = first['sku']

    issued_date = data.get('issued_date') or datetime.utcnow().date().isoformat()
    notes = data.get('notes') or None

    with get_connection() as conn:
        # Resolve supplier.
        supplier_id = data.get('supplier_id')
        if supplier_id in ('', None):
            supplier_id = None
        if supplier_id is None and (data.get('supplier_name') or '').strip():
            supplier_id = _upsert_supplier(conn, {'name': data['supplier_name']})

        po_id = next_po_id(conn)

        store_breakdown = _has_column(conn, 'purchase_orders', 'size_breakdown')
        store_products = _has_column(conn, 'purchase_orders', 'po_products')

        cols = """
                po_id, sku, product_name, supplier_id,
                quantity_ordered, unit_cost_bdt, total_cost_bdt,
                advance_paid_bdt, balance_due_bdt, total_paid_bdt,
                current_stage, stage_completion_pct, po_status,
                issued_date, due_date, expected_delivery,
                triggered_by, notes"""
        vals = """
                :po_id, :sku, :product_name, :supplier_id,
                :quantity, :unit_cost, :total_cost,
                :advance_paid, :balance_due, :advance_paid2,
                'PO Issued', 0, 'Active',
                :issued_date, :due_date, :due_date2,
                'manual', :notes"""
        params = {
            'po_id': po_id,
            'sku': sku,
            'product_name': product_name,
            'supplier_id': supplier_id,
            'quantity': total_qty,
            'unit_cost': unit_cost,
            'total_cost': total_cost,
            'advance_paid': advance_paid,
            'balance_due': balance_due,
            'advance_paid2': advance_paid,
            'issued_date': issued_date,
            'due_date': due_date,
            'due_date2': due_date,
            'notes': notes,
        }
        if store_breakdown and first.get('size_breakdown') is not None:
            cols += ", size_breakdown"
            vals += ", CAST(:size_breakdown AS JSONB)"
            params['size_breakdown'] = json.dumps(first['size_breakdown'])
        if store_products:
            cols += ", po_products"
            vals += ", CAST(:po_products AS JSONB)"
            params['po_products'] = json.dumps(products)

        conn.execute(text(
            "INSERT INTO purchase_orders (" + cols + ") VALUES (" + vals + ")"
        ), params)

        # Initial timeline event.
        tl_id = next_tl_id(conn)
        if len(products) == 1:
            note = str(total_qty) + ' pcs of ' + first['product_name'] + ' ordered.'
        else:
            note = (str(total_qty) + ' pcs across ' + str(len(products))
                    + ' products ordered: '
                    + ', '.join(p['product_name'] for p in products) + '.')
        conn.execute(text("""
            INSERT INTO po_timeline (
                event_id, po_id, stage, event_title, event_note,
                source_type, source_ref, logged_by
            ) VALUES (
                :event_id, :po_id, 'PO Issued', 'PO generated', :note,
                'brain', :po_id2, 'Brain auto-generated'
            )
        """), {
            'event_id': tl_id,
            'po_id': po_id,
            'note': note,
            'po_id2': po_id,
        })

        conn.commit()

    return po_id


def log_pm_event(po_id, data):
    """
    Log a production-manager milestone against a PO.

    data keys: stage, status, note, est_date, units_done, logged_by.
    Updates the PO stage / completion / status as appropriate and returns
    {event_id, new_status, new_stage}.
    """
    stage = (data.get('stage') or '').strip()
    status = (data.get('status') or 'In progress').strip()
    note = data.get('note') or None
    est_date = data.get('est_date') or None
    if est_date == '':
        est_date = None
    logged_by = (data.get('logged_by') or 'Production Manager').strip()

    try:
        units_done = int(data.get('units_done') or 0)
    except (TypeError, ValueError):
        units_done = 0

    # Normalise a couple of UI labels onto canonical stage names.
    stage_alias = {'Delivery': 'Delivered'}
    canonical_stage = stage_alias.get(stage, stage)

    with get_connection() as conn:
        po = conn.execute(text("""
            SELECT current_stage, stage_completion_pct, po_status
            FROM purchase_orders WHERE po_id = :po_id
        """), {'po_id': po_id}).fetchone()
        if po is None:
            raise ValueError('PO not found: ' + str(po_id))

        current_stage = po[0]

        event_title = (canonical_stage or 'Update') + ' — ' + status
        is_alert = status in ('Issue flagged', 'Delayed')
        alert_severity = 'high' if status == 'Issue flagged' else (
            'medium' if status == 'Delayed' else None)

        tl_id = next_tl_id(conn)
        conn.execute(text("""
            INSERT INTO po_timeline (
                event_id, po_id, stage, event_title, event_note,
                source_type, source_ref, logged_by, is_alert, alert_severity
            ) VALUES (
                :event_id, :po_id, :stage, :title, :note,
                'pm', :source_ref, :logged_by, :is_alert, :alert_severity
            )
        """), {
            'event_id': tl_id,
            'po_id': po_id,
            'stage': canonical_stage or current_stage,
            'title': event_title,
            'note': note,
            'source_ref': ('Units done: ' + str(units_done)) if units_done else None,
            'logged_by': logged_by,
            'is_alert': is_alert,
            'alert_severity': alert_severity,
        })

        new_stage = current_stage

        # If this milestone names a known stage, move the PO to it.
        if canonical_stage in STAGE_PCT:
            target_stage = canonical_stage
            # On "Completed" of the current stage, advance to the next one.
            if status == 'Completed' and canonical_stage != 'Delivered':
                idx = STAGES.index(canonical_stage)
                if idx + 1 < len(STAGES):
                    target_stage = STAGES[idx + 1]
            new_stage = target_stage
            conn.execute(text("""
                UPDATE purchase_orders
                SET current_stage        = :stage,
                    stage_completion_pct = :pct,
                    actual_delivery = CASE WHEN :stage = 'Delivered'
                                           THEN COALESCE(actual_delivery, CURRENT_DATE)
                                           ELSE actual_delivery END,
                    updated_at           = NOW()
                WHERE po_id = :po_id
            """), {
                'stage': new_stage,
                'pct': STAGE_PCT[new_stage],
                'po_id': po_id,
            })
        else:
            conn.execute(text("""
                UPDATE purchase_orders SET updated_at = NOW() WHERE po_id = :po_id
            """), {'po_id': po_id})

        # If the PM explicitly flagged a delay, force Delayed status before the
        # rule-based recalculation refines it.
        if status == 'Delayed':
            conn.execute(text("""
                UPDATE purchase_orders SET po_status = 'Delayed', updated_at = NOW()
                WHERE po_id = :po_id
            """), {'po_id': po_id})

        new_status = recalculate_po_status(conn, po_id)
        conn.commit()

    return {'event_id': tl_id, 'new_status': new_status, 'new_stage': new_stage}


def recalculate_po_status(conn, po_id):
    """
    Recompute and persist po_status for a single PO based on the rules:
      - delivered/overdue (days_overdue > 0)               -> 'Delayed'
      - due within 7 days AND stage not in (QC, Delivered) -> 'At Risk'
      - last update > 4 days ago AND currently 'Active'    -> 'At Risk'
      - otherwise                                          -> 'Active'
    Completed / Cancelled POs are left untouched. Returns the resulting status.
    """
    row = conn.execute(text("""
        SELECT
            po.current_stage,
            po.po_status,
            CASE WHEN po.actual_delivery IS NULL
                 THEN GREATEST((CURRENT_DATE - po.due_date)::INTEGER, 0)
                 ELSE 0 END AS days_overdue,
            (po.due_date - CURRENT_DATE)::INTEGER AS days_to_due,
            (SELECT MAX(event_date) FROM po_timeline t WHERE t.po_id = po.po_id)
                AS last_update
        FROM purchase_orders po
        WHERE po.po_id = :po_id
    """), {'po_id': po_id}).fetchone()

    if row is None:
        return None

    current_stage, po_status, days_overdue, days_to_due, last_update = row

    # Don't disturb terminal states.
    if po_status in ('Completed', 'Cancelled'):
        return po_status

    new_status = 'Active'
    if days_overdue and days_overdue > 0:
        new_status = 'Delayed'
    elif (days_to_due is not None and days_to_due <= 7
          and current_stage not in ('QC', 'Delivered')):
        new_status = 'At Risk'
    else:
        stale = False
        if last_update is not None:
            delta = datetime.utcnow() - last_update
            stale = delta.days > 4
        if stale and po_status == 'Active':
            new_status = 'At Risk'

    conn.execute(text("""
        UPDATE purchase_orders
        SET po_status = :status, updated_at = NOW()
        WHERE po_id = :po_id
    """), {'status': new_status, 'po_id': po_id})

    # FIX 3: keep reorder_queue in sync with every po_status change.
    if new_status in ('Completed', 'Cancelled'):
        conn.execute(text("""
            UPDATE reorder_queue
            SET po_created = FALSE, po_id = NULL, po_status_display = NULL
            WHERE po_id = :po_id
        """), {'po_id': po_id})
    else:
        conn.execute(text("""
            UPDATE reorder_queue
            SET po_status_display = :disp
            WHERE po_id = :po_id
        """), {'disp': 'PO ' + new_status, 'po_id': po_id})

    return new_status


def update_po(po_id, data):
    """Update editable fields of a PO."""
    product_name = (data.get('product_name') or '').strip()
    if not product_name:
        raise ValueError('Product name is required')
    try:
        quantity = int(data.get('quantity_ordered') or 0)
    except (TypeError, ValueError):
        raise ValueError('Quantity must be a number')
    if quantity <= 0:
        raise ValueError('Quantity must be greater than zero')
    due_date = data.get('due_date')
    if not due_date:
        raise ValueError('Due date is required')
    unit_cost = data.get('unit_cost_bdt')
    unit_cost = float(unit_cost) if unit_cost not in (None, '') else 0.0
    advance_paid = data.get('advance_paid_bdt')
    advance_paid = float(advance_paid) if advance_paid not in (None, '') else 0.0
    total_cost = unit_cost * quantity
    balance_due = total_cost - advance_paid
    sku = data.get('sku') or None
    if sku == '':
        sku = None
    notes = data.get('notes') or None
    with get_connection() as conn:
        supplier_id = None
        if (data.get('supplier_name') or '').strip():
            supplier_id = _upsert_supplier(conn, {'name': data['supplier_name']})
        conn.execute(text("""
            UPDATE purchase_orders
            SET product_name     = :product_name,
                sku              = :sku,
                supplier_id      = COALESCE(:supplier_id, supplier_id),
                quantity_ordered = :quantity,
                unit_cost_bdt    = :unit_cost,
                total_cost_bdt   = :total_cost,
                advance_paid_bdt = :advance_paid,
                balance_due_bdt  = :balance_due,
                due_date         = :due_date,
                expected_delivery= :due_date2,
                notes            = :notes,
                updated_at       = NOW()
            WHERE po_id = :po_id
        """), {
            'product_name': product_name, 'sku': sku, 'supplier_id': supplier_id,
            'quantity': quantity, 'unit_cost': unit_cost, 'total_cost': total_cost,
            'advance_paid': advance_paid, 'balance_due': balance_due,
            'due_date': due_date, 'due_date2': due_date, 'notes': notes, 'po_id': po_id,
        })
        conn.commit()


def delete_po(po_id):
    """Delete a PO and all its timeline events, and clear the inventory link."""
    with get_connection() as conn:
        # Clear inventory link before the row is deleted (belt-and-suspenders;
        # the FK ON DELETE SET NULL + trigger also handles this automatically).
        conn.execute(text("""
            UPDATE reorder_queue
            SET po_created = FALSE, po_id = NULL, po_status_display = NULL
            WHERE po_id = :po_id
        """), {'po_id': po_id})
        conn.execute(text("DELETE FROM po_timeline WHERE po_id = :po_id"), {'po_id': po_id})
        conn.execute(text("DELETE FROM purchase_orders WHERE po_id = :po_id"), {'po_id': po_id})
        conn.commit()


def get_suppliers_with_stats():
    """Return all suppliers with active PO count, completed count, and on_time_pct."""
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT s.*,
                   COUNT(po.po_id) FILTER (
                       WHERE po.po_status NOT IN ('Completed','Cancelled')
                   ) AS active_po_count,
                   CASE WHEN s.total_pos > 0
                        THEN ROUND(s.on_time_count::numeric / s.total_pos * 100, 1)
                        ELSE 0
                   END AS on_time_pct
            FROM suppliers s
            LEFT JOIN purchase_orders po ON po.supplier_id = s.id
            GROUP BY s.id
            ORDER BY s.reliability_score DESC NULLS LAST, s.name ASC
        """)).fetchall()
    return _rows_to_list(rows)


def get_supplier_detail(supplier_id):
    """Return supplier dict plus full PO history, or None if not found."""
    with get_connection() as conn:
        sup_row = conn.execute(text(
            "SELECT * FROM suppliers WHERE id = :id"
        ), {'id': supplier_id}).fetchone()
        if sup_row is None:
            return None
        po_rows = conn.execute(text("""
            SELECT po.*,
                   (SELECT COUNT(*) FROM po_timeline t WHERE t.po_id = po.po_id)
                       AS event_count,
                   (SELECT MAX(event_date) FROM po_timeline t WHERE t.po_id = po.po_id)
                       AS last_update
            FROM purchase_orders po
            WHERE po.supplier_id = :sid
            ORDER BY po.issued_date DESC
        """), {'sid': supplier_id}).fetchall()
    return {
        'supplier': _row_to_dict(sup_row),
        'pos': _rows_to_list(po_rows),
    }


def receive_po_stock(po_id, data):
    """
    Mark a PO as delivered and received at the warehouse.

    Updates purchase_orders, skus.current_stock, suppliers stats,
    and logs a Delivered timeline event.
    Returns {success, event_id, new_status}.
    """
    try:
        units_received = int(data.get('units_received') or 0)
    except (TypeError, ValueError):
        raise ValueError('Units received must be a number')
    if units_received < 0:
        raise ValueError('Units received cannot be negative')

    try:
        units_rejected = int(data.get('units_rejected') or 0)
    except (TypeError, ValueError):
        units_rejected = 0

    notes = data.get('notes') or None
    today = datetime.utcnow().date()

    with get_connection() as conn:
        po_row = conn.execute(text("""
            SELECT po.*, s.id AS s_id
            FROM purchase_orders po
            LEFT JOIN suppliers s ON s.id = po.supplier_id
            WHERE po.po_id = :po_id
        """), {'po_id': po_id}).fetchone()
        if po_row is None:
            raise ValueError('PO not found: ' + str(po_id))

        po = _row_to_dict(po_row)
        due_date_raw = po.get('due_date')
        on_time = True
        if due_date_raw:
            try:
                due = date.fromisoformat(str(due_date_raw)[:10])
                on_time = today <= due
            except Exception:
                pass

        conn.execute(text("""
            UPDATE purchase_orders SET
                quantity_received    = :qr,
                quantity_rejected    = :qrej,
                actual_delivery      = :today,
                current_stage        = 'Delivered',
                stage_completion_pct = 100,
                po_status            = 'Completed',
                updated_at           = NOW()
            WHERE po_id = :po_id
        """), {
            'qr': units_received, 'qrej': units_rejected,
            'today': today.isoformat(), 'po_id': po_id,
        })

        tl_id = next_tl_id(conn)
        ev_note = str(units_received) + ' units received'
        if units_rejected > 0:
            ev_note += ', ' + str(units_rejected) + ' rejected'
        if notes:
            ev_note += '. ' + notes

        conn.execute(text("""
            INSERT INTO po_timeline (
                event_id, po_id, stage, event_title, event_note,
                source_type, logged_by
            ) VALUES (
                :eid, :po_id, 'Delivered', 'Stock received at warehouse', :note,
                'pm', 'Warehouse (GRN)'
            )
        """), {'eid': tl_id, 'po_id': po_id, 'note': ev_note})

        # Update skus.current_stock for the PO's linked SKU
        sku = po.get('sku')
        net_received = max(0, units_received - units_rejected)
        if sku and net_received > 0:
            conn.execute(text("""
                UPDATE skus
                SET current_stock = current_stock + :qty, updated_at = NOW()
                WHERE sku = :sku
            """), {'qty': net_received, 'sku': sku})

        # Update supplier reliability stats
        supplier_id = po.get('supplier_id') or po.get('s_id')
        if supplier_id:
            if on_time:
                conn.execute(text("""
                    UPDATE suppliers SET
                        on_time_count  = on_time_count + 1,
                        completed_pos  = completed_pos + 1,
                        total_pos      = total_pos + 1,
                        last_po_date   = :today,
                        updated_at     = NOW()
                    WHERE id = :sid
                """), {'today': today.isoformat(), 'sid': supplier_id})
            else:
                conn.execute(text("""
                    UPDATE suppliers SET
                        delayed_count  = delayed_count + 1,
                        completed_pos  = completed_pos + 1,
                        total_pos      = total_pos + 1,
                        last_po_date   = :today,
                        updated_at     = NOW()
                    WHERE id = :sid
                """), {'today': today.isoformat(), 'sid': supplier_id})

            conn.execute(text("""
                UPDATE suppliers SET
                    reliability_score = ROUND(
                        LEAST(10.00, GREATEST(0.00,
                            (on_time_count::numeric / NULLIF(total_pos,0)) * 10 * 0.7
                            + GREATEST(0,
                                10 - (quality_issue_count::numeric / NULLIF(total_pos,0)) * 10
                              ) * 0.3
                        )), 2),
                    updated_at = NOW()
                WHERE id = :sid
            """), {'sid': supplier_id})

        # FIX 3: Stock arrived — clear the PO link from reorder_queue so the
        # next engine run recalculates the SKU's need with the new stock level.
        conn.execute(text("""
            UPDATE reorder_queue
            SET po_created = FALSE, po_id = NULL, po_status_display = NULL
            WHERE po_id = :po_id
        """), {'po_id': po_id})

        conn.commit()

    return {'success': True, 'event_id': tl_id, 'new_status': 'Completed'}


def get_waiting_orders(po_id):
    """
    Return orders linked to this PO via linked_so_numbers.
    Also returns a summary of units needed vs arriving.
    """
    with get_connection() as conn:
        po_row = conn.execute(text("""
            SELECT po_id, product_name, sku, quantity_ordered,
                   quantity_received, linked_so_numbers
            FROM purchase_orders WHERE po_id = :po_id
        """), {'po_id': po_id}).fetchone()
        if po_row is None:
            return None
        po = _row_to_dict(po_row)

        linked = po.get('linked_so_numbers') or []
        orders = []
        if linked:
            rows = conn.execute(text("""
                SELECT so_number, customer_name, customer_phone,
                       total_receivable, nuport_status, payment_status,
                       order_date,
                       EXTRACT(DAY FROM NOW() - order_date)::INTEGER AS days_waiting
                FROM orders
                WHERE so_number = ANY(:so_list)
                ORDER BY order_date ASC
            """), {'so_list': linked}).fetchall()
            orders = _rows_to_list(rows)

    return {
        'po': po,
        'orders': orders,
        'order_count': len(orders),
        'revenue_held': sum((o.get('total_receivable') or 0) for o in orders),
    }


def link_so_to_po(po_id, so_number):
    """Append a SO number to a PO's linked_so_numbers array."""
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE purchase_orders
            SET linked_so_numbers = array_append(
                    COALESCE(linked_so_numbers, ARRAY[]::TEXT[]), :so),
                updated_at = NOW()
            WHERE po_id = :po_id
              AND NOT (:so = ANY(COALESCE(linked_so_numbers, ARRAY[]::TEXT[])))
        """), {'so': so_number, 'po_id': po_id})
        conn.commit()


def unlink_so_from_po(po_id, so_number):
    """Remove a SO number from a PO's linked_so_numbers array."""
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE purchase_orders
            SET linked_so_numbers = array_remove(
                    COALESCE(linked_so_numbers, ARRAY[]::TEXT[]), :so),
                updated_at = NOW()
            WHERE po_id = :po_id
        """), {'so': so_number, 'po_id': po_id})
        conn.commit()


# ── product picker + Brain quantity matrix ───────────────────────────────────

def search_products(query, limit=20):
    """
    Search active products for the New PO picker.

    Groups all size variants into one 'product' by stripping the size suffix
    from product_name using the same regex the Products module uses.
    e.g. "Dusty Olive … - 30", "Dusty Olive … - 32" → one row "Dusty Olive …"
    """
    q = (query or '').strip()
    like = '%' + q + '%'
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT base_name,
                   COUNT(*)                        AS variant_count,
                   COALESCE(SUM(current_stock), 0) AS total_stock,
                   AVG(cost_price)                 AS avg_cost
            FROM (
                SELECT TRIM(regexp_replace(product_name, :size_re, '', 'i')) AS base_name,
                       current_stock, cost_price
                FROM skus
                WHERE is_active = TRUE
                  AND product_name IS NOT NULL
                  AND (product_name ILIKE :like OR sku ILIKE :like)
            ) t
            GROUP BY base_name
            ORDER BY base_name ASC
            LIMIT :limit
        """), {'like': like, 'size_re': _SIZE_RE_SQL, 'limit': int(limit)}).fetchall()
    return _rows_to_list(rows)


def get_product_matrix(product_name, color=None, lead_time_days=None):
    """
    Return the per-size Brain matrix for the New PO form.

    Source of truth is the Inventory module's reorder_queue (the ONE formula
    lives in inventory/reorder_engine.py — Supply Chain never recalculates).
    If the queue has no row yet (engine not run, or a brand-new product), fall
    back to a live computation so the form still works.
    """
    pname = (product_name or '').strip()
    if not pname:
        raise ValueError('product_name is required')
    with get_connection() as conn:
        row = conn.execute(text(
            "SELECT * FROM reorder_queue WHERE sku_base = :b"
        ), {'b': pname}).fetchone()
        if row is not None:
            return _matrix_from_queue(conn, _row_to_dict(row))
    return _live_product_matrix(pname, lead_time_days)


def _matrix_from_queue(conn, r):
    """Transform a reorder_queue row (dicts keyed by size) into the array shape
    the New PO matrix UI expects. Reads stored quantities — no recompute."""
    size_bd  = r.get('size_breakdown') or {}
    stock_bd = r.get('current_stock_breakdown') or {}
    sales_bd = r.get('sales_30d_breakdown') or {}
    net_bd   = r.get('net_need_breakdown') or {}
    wait_bd  = r.get('waiting_orders_breakdown') or {}

    sizes = sorted(set(list(size_bd) + list(stock_bd) + list(sales_bd)),
                   key=_size_sort_key)

    # Map size label -> sku + cost for display, from the live skus table.
    sku_rows = conn.execute(text("""
        SELECT sku, product_name, COALESCE(size,'') AS raw_size, cost_price
        FROM skus
        WHERE is_active = TRUE
          AND TRIM(regexp_replace(product_name, :size_re, '', 'i')) = :pname
    """), {'pname': r.get('sku_base'), 'size_re': _SIZE_RE_SQL}).fetchall()
    sku_by_size, costs = {}, []
    for sr in sku_rows:
        m = sr._mapping
        lbl = _extract_size_label(m['product_name'], m['raw_size'])
        sku_by_size[lbl] = m['sku']
        if m['cost_price']:
            costs.append(float(m['cost_price']))

    auto_qty       = [int(size_bd.get(s, 0)) for s in sizes]
    current_stock  = [int(stock_bd.get(s, 0)) for s in sizes]
    sales_30d      = [int(sales_bd.get(s, 0)) for s in sizes]
    net_need       = [int(net_bd.get(s, 0)) for s in sizes]
    waiting_orders = [int(wait_bd.get(s, 0)) for s in sizes]
    daily_velocity = [round(v / 30.0, 2) for v in sales_30d]
    skus           = [sku_by_size.get(s, '') for s in sizes]
    unit_cost      = round(sum(costs) / len(costs), 2) if costs else 0.0

    return {
        'sku_base': _common_sku_prefix([s for s in skus if s]),
        'product_name': r.get('sku_base'),
        'lead_time_days': DEFAULT_LEAD_TIME_DAYS,
        'runway_days': TARGET_RUNWAY_DAYS,
        'min_per_size': MIN_PER_SIZE,
        'coverage_days': DEFAULT_LEAD_TIME_DAYS + TARGET_RUNWAY_DAYS,
        'unit_cost_bdt': unit_cost,
        'sizes': sizes,
        'skus': skus,
        'auto_qty': auto_qty,
        'net_need': net_need,
        'daily_velocity': daily_velocity,
        'current_stock': current_stock,
        'sales_30d': sales_30d,
        'waiting_orders': waiting_orders,
        'total_auto': int(r.get('recommended_total') or sum(auto_qty)),
        'total_30d_sales': sum(sales_30d),
        'total_stock': sum(current_stock),
        'source': 'reorder_queue',
    }


def _live_product_matrix(product_name, lead_time_days=None):
    """
    Build the per-size Brain quantity recommendation for a product (live).

    Finds all active SKUs whose base_name (product_name with size suffix stripped)
    matches `product_name`, extracts size labels from product_name, then computes
    per size (target-stock model):

        coverage_days = lead_time + TARGET_RUNWAY_DAYS
        demand        = ceil(daily_velocity * coverage_days)
        net_need      = max(0, demand - current_stock)
        order_qty     = net_need + waiting_orders   (+ MOQ floor when the size sells)

    This produces enough so that, once the goods arrive after `lead_time`, the
    size still holds ~TARGET_RUNWAY_DAYS of forward stock.

    30-day sales includes: Delivered, On Hold, In Transit, Pending — i.e. all
    statuses EXCEPT Cancelled / Returned / Refunded / Rejected.
    """
    import math

    pname = (product_name or '').strip()
    if not pname:
        raise ValueError('product_name is required')
    try:
        lead = int(lead_time_days) if lead_time_days not in (None, '') else DEFAULT_LEAD_TIME_DAYS
    except (TypeError, ValueError):
        lead = DEFAULT_LEAD_TIME_DAYS
    if lead <= 0:
        lead = DEFAULT_LEAD_TIME_DAYS

    with get_connection() as conn:
        sku_rows = conn.execute(text("""
            SELECT sku, product_name,
                   COALESCE(size, '') AS raw_size,
                   COALESCE(current_stock, 0) AS current_stock,
                   cost_price
            FROM skus
            WHERE is_active = TRUE
              AND TRIM(regexp_replace(product_name, :size_re, '', 'i')) = :pname
        """), {'pname': pname, 'size_re': _SIZE_RE_SQL}).fetchall()

        if not sku_rows:
            raise ValueError('No active SKUs found for "' + pname + '"')

        variants = _rows_to_list(sku_rows)
        for v in variants:
            v['size_label'] = _extract_size_label(v.get('product_name', ''), v.get('raw_size', ''))
        variants.sort(key=lambda v: _size_sort_key(v.get('size_label')))
        skus = [v['sku'] for v in variants]

        # 30-day sales per SKU — include Delivered + On Hold + In Transit + Pending.
        # Exclude only definitive negative outcomes.
        sales_rows = conn.execute(text("""
            SELECT oi.sku, COALESCE(SUM(oi.quantity), 0) AS qty
            FROM order_items oi
            JOIN orders o ON o.so_number = oi.so_number
            WHERE oi.sku = ANY(:skus)
              AND o.order_date IS NOT NULL
              AND o.order_date >= NOW() - INTERVAL '30 days'
              AND COALESCE(o.nuport_status, '') NOT ILIKE '%cancel%'
              AND COALESCE(o.nuport_status, '') NOT ILIKE '%return%'
              AND COALESCE(o.nuport_status, '') NOT ILIKE '%refund%'
              AND COALESCE(o.nuport_status, '') NOT ILIKE '%reject%'
            GROUP BY oi.sku
        """), {'skus': skus}).fetchall()
        sales_map = {r._mapping['sku']: int(r._mapping['qty'] or 0) for r in sales_rows}

        # Waiting orders — only Pending and On Hold count.
        # Status values in the DB are like 'PENDING', 'REQUESTED', 'ON_HOLD'
        # (underscore, not space). These patterns mirror the Orders module
        # definitions in app.py so the numbers match the dashboard exactly.
        wait_rows = conn.execute(text("""
            SELECT oi.sku, COALESCE(SUM(oi.quantity), 0) AS qty
            FROM order_items oi
            JOIN orders o ON o.so_number = oi.so_number
            WHERE oi.sku = ANY(:skus)
              AND (
                UPPER(COALESCE(o.nuport_status, '')) IN ('PENDING', 'REQUESTED')
                OR COALESCE(o.nuport_status, '') ILIKE 'on%hold'
              )
            GROUP BY oi.sku
        """), {'skus': skus}).fetchall()
        wait_map = {r._mapping['sku']: int(r._mapping['qty'] or 0) for r in wait_rows}

    sizes, stock, sales, waiting = [], [], [], []
    cost_vals = []
    for v in variants:
        sizes.append(v.get('size_label') or '—')
        stock.append(int(v.get('current_stock') or 0))
        sales.append(sales_map.get(v['sku'], 0))
        waiting.append(wait_map.get(v['sku'], 0))
        if v.get('cost_price'):
            cost_vals.append(float(v['cost_price']))

    total_sales = sum(sales)
    total_stock = sum(stock)

    # ── Part 18: per-size independent deficit calculation ───────────────────
    # Each size is sized on its OWN demand vs stock; total is the RESULT.
    # Target-stock model: cover the production wait PLUS a forward runway, so
    # the size still holds ~TARGET_RUNWAY_DAYS of stock once the goods land.
    coverage_days = lead + TARGET_RUNWAY_DAYS
    auto_qty = []
    net_need = []
    daily_velocity = []
    for i in range(len(sizes)):
        s30 = sales[i]
        daily = s30 / 30.0
        daily_velocity.append(round(daily, 2))
        demand = math.ceil(daily * coverage_days)
        need = max(0, demand - stock[i])
        net_need.append(need)

        order_qty = need + waiting[i]
        if s30 > 0 and order_qty < MIN_PER_SIZE:
            order_qty = MIN_PER_SIZE      # MOQ floor for live sizes
        if s30 == 0:
            order_qty = 0                 # never order a dead size
        auto_qty.append(int(order_qty))

    unit_cost = round(sum(cost_vals) / len(cost_vals), 2) if cost_vals else 0.0
    sku_base = _common_sku_prefix(skus)

    return {
        'sku_base': sku_base,
        'product_name': pname,
        'lead_time_days': lead,
        'runway_days': TARGET_RUNWAY_DAYS,
        'min_per_size': MIN_PER_SIZE,
        'coverage_days': coverage_days,
        'unit_cost_bdt': unit_cost,
        'sizes': sizes,
        'skus': skus,
        'auto_qty': auto_qty,
        'net_need': net_need,
        'daily_velocity': daily_velocity,
        'current_stock': stock,
        'sales_30d': sales,
        'waiting_orders': waiting,
        'total_auto': sum(auto_qty),
        'total_30d_sales': total_sales,
        'total_stock': total_stock,
    }


def _common_sku_prefix(skus):
    """Best-effort common base for a set of size SKUs (for display only)."""
    if not skus:
        return ''
    if len(skus) == 1:
        return skus[0]
    s1, s2 = min(skus), max(skus)
    i = 0
    while i < len(s1) and i < len(s2) and s1[i] == s2[i]:
        i += 1
    base = s1[:i].rstrip('-_ ')
    return base or skus[0]
