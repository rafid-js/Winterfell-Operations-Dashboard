"""
Supply Chain — data access layer (Phase 1).

All functions return plain Python dicts / lists of dicts (never SQLAlchemy Row
objects) and convert Decimal -> float and date/datetime -> ISO strings so the
results are JSON-serialisable straight out of the route handlers.

Queries use parameterised text() — never string-format user input into SQL.
"""
import os
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


def create_po(data):
    """
    Create a purchase order plus its initial 'PO generated' timeline event.

    Required: product_name, quantity_ordered, due_date.
    Supplier may be passed as supplier_id or supplier_name (upsert by name).
    Returns the generated po_id.
    """
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

        conn.execute(text("""
            INSERT INTO purchase_orders (
                po_id, sku, product_name, supplier_id,
                quantity_ordered, unit_cost_bdt, total_cost_bdt,
                advance_paid_bdt, balance_due_bdt, total_paid_bdt,
                current_stage, stage_completion_pct, po_status,
                issued_date, due_date, expected_delivery,
                triggered_by, notes
            ) VALUES (
                :po_id, :sku, :product_name, :supplier_id,
                :quantity, :unit_cost, :total_cost,
                :advance_paid, :balance_due, :advance_paid2,
                'PO Issued', 0, 'Active',
                :issued_date, :due_date, :due_date2,
                'manual', :notes
            )
        """), {
            'po_id': po_id,
            'sku': sku,
            'product_name': product_name,
            'supplier_id': supplier_id,
            'quantity': quantity,
            'unit_cost': unit_cost,
            'total_cost': total_cost,
            'advance_paid': advance_paid,
            'balance_due': balance_due,
            'advance_paid2': advance_paid,
            'issued_date': issued_date,
            'due_date': due_date,
            'due_date2': due_date,
            'notes': notes,
        })

        # Initial timeline event.
        tl_id = next_tl_id(conn)
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
            'note': str(quantity) + ' pcs of ' + product_name + ' ordered.',
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
    """Delete a PO and all its timeline events."""
    with get_connection() as conn:
        conn.execute(text("DELETE FROM po_timeline WHERE po_id = :po_id"), {'po_id': po_id})
        conn.execute(text("DELETE FROM purchase_orders WHERE po_id = :po_id"), {'po_id': po_id})
        conn.commit()
