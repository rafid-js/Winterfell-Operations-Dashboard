"""Ad Allocator — Meta budget push with guardrails (spec §8, §10).

Flow: an allocation run yields increase/decrease rows → build_change_requests
turns each mapped ad set into a PENDING aa_change_request → a human approves or
rejects in the UI → push_approved writes approved ones to Meta.

Guardrails enforced at push time (never bypassed):
  • max ±max_change_pct (20%) change per ad set per day — avoid resetting learning
  • never push an ad set with fewer than min_days_data (2) days of data
  • warn (not block) if the new budget would starve Meta's ~50 purchases/week
    learning threshold
  • amounts converted to minor units at the Meta boundary (৳15,000 → 1500000)
"""
import json
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'brain'))
from apis.meta import meta  # noqa: E402

from . import config_store  # noqa: E402
from .data import RAGNAR_ACCOUNT_ID  # noqa: E402

MINOR = 100  # BDT → minor units


def _clamp_to_daily_guardrail(current_daily, target_daily, max_pct):
    """Clamp a target daily budget to ±max_pct of the current daily budget."""
    if not current_daily or current_daily <= 0:
        return target_daily, False
    lo, hi = current_daily * (1 - max_pct), current_daily * (1 + max_pct)
    clamped = max(lo, min(hi, target_daily))
    return clamped, (abs(clamped - target_daily) > 0.01)


def _adset_days_of_data(conn, adset_id, days=14):
    return conn.execute(text("""
        SELECT COUNT(DISTINCT date) FROM ad_spend
        WHERE account_id = :acct AND adset_id = :a AND date > NOW() - (:d || ' days')::interval
    """), {'acct': RAGNAR_ACCOUNT_ID, 'a': str(adset_id), 'd': days}).scalar() or 0


def _recent_cpp(conn, adset_id, days=14):
    row = conn.execute(text("""
        SELECT SUM(COALESCE(spend_bdt,0)) AS s, SUM(COALESCE(orders_attributed,0)) AS o
        FROM ad_spend WHERE account_id = :acct AND adset_id = :a
          AND date > NOW() - (:d || ' days')::interval
    """), {'acct': RAGNAR_ACCOUNT_ID, 'a': str(adset_id), 'd': days}).fetchone()
    s, o = float(row._mapping['s'] or 0), float(row._mapping['o'] or 0)
    return (s / o) if o else 0.0


def build_change_requests(conn, run_id):
    """Create PENDING change requests for increase/decrease rows that map to
    ad sets. Returns the number created. Current budgets are fetched live when
    Meta is reachable; otherwise from_amount is left null and resolved at push."""
    cfg = config_store.load_config(conn)
    rows = conn.execute(text("""
        SELECT row.product_group_id, row.rec_amount, row.action
        FROM aa_allocation_row row
        WHERE row.run_id = :run AND row.action IN ('increase','decrease')
    """), {'run': run_id}).fetchall()

    created = 0
    for r in rows:
        m = r._mapping
        adsets = conn.execute(text("""
            SELECT meta_adset_id, weight FROM aa_ad_product_map
            WHERE product_group_id = :g AND meta_adset_id IS NOT NULL
        """), {'g': m['product_group_id']}).fetchall()
        for a in adsets:
            adset_id = a._mapping['meta_adset_id']
            weight = float(a._mapping['weight'] or 1)
            target_daily = float(m['rec_amount']) * weight / 7.0  # weekly → daily

            from_amount = None
            try:
                info = meta.get_adset(adset_id)
                db = info.get('daily_budget')
                if db is not None:
                    from_amount = float(db) / MINOR
            except Exception as e:  # noqa: BLE001 — resolved at push
                print(f'  ⚠ could not read ad set {adset_id} budget: {e}', flush=True)

            conn.execute(text("""
                INSERT INTO aa_change_request
                  (run_id, product_group_id, ad_set_id, from_amount, to_amount, status)
                VALUES (:run, :g, :a, :fr, :to, 'pending')
            """), {'run': run_id, 'g': m['product_group_id'], 'a': str(adset_id),
                   'fr': from_amount, 'to': round(target_daily, 2)})
            created += 1
    conn.commit()
    return created


def list_change_requests(conn, run_id=None, status=None):
    q = """SELECT cr.*, g.name AS group_name FROM aa_change_request cr
           LEFT JOIN aa_product_group g ON cr.product_group_id = g.id WHERE 1=1"""
    params = {}
    if run_id is not None:
        q += " AND cr.run_id = :run"; params['run'] = run_id
    if status:
        q += " AND cr.status = :st"; params['st'] = status
    q += " ORDER BY cr.created_at DESC"
    return [dict(r._mapping) for r in conn.execute(text(q), params).fetchall()]


def set_status(conn, cr_id, status, who=None):
    conn.execute(text("""
        UPDATE aa_change_request
        SET status = :st, approved_by = :who,
            approved_at = CASE WHEN :st IN ('approved','rejected') THEN NOW() ELSE approved_at END
        WHERE id = :id AND status = 'pending'
    """), {'st': status, 'who': who, 'id': cr_id})
    conn.commit()


def push_approved(conn):
    """Push every APPROVED change request to Meta, enforcing guardrails. Returns
    a per-request result list. A request that trips a hard guardrail is marked
    failed with the reason; it is never silently skipped."""
    cfg = config_store.load_config(conn)
    max_pct = float(cfg['max_change_pct'])
    min_days = int(cfg['min_days_data'])
    learn_thr = float(cfg['learning_purchases_wk'])

    crs = list_change_requests(conn, status='approved')
    results = []
    for cr in crs:
        cr_id, adset_id = cr['id'], cr['ad_set_id']
        target_daily = float(cr['to_amount'] or 0)

        # Guardrail: minimum days of data.
        days = _adset_days_of_data(conn, adset_id)
        if days < min_days:
            _fail(conn, cr_id, f'Only {days}d of data (<{min_days}d) — not pushed')
            results.append({'id': cr_id, 'status': 'failed', 'reason': 'insufficient_data'})
            continue

        # Resolve current budget live, then clamp to ±max_pct/day.
        try:
            info = meta.get_adset(adset_id)
            current_daily = float(info.get('daily_budget') or 0) / MINOR
        except Exception as e:  # noqa: BLE001
            _fail(conn, cr_id, f'Could not read current budget: {e}')
            results.append({'id': cr_id, 'status': 'failed', 'reason': 'read_error'})
            continue

        final_daily, was_clamped = _clamp_to_daily_guardrail(current_daily, target_daily, max_pct)

        # Learning-threshold warning (not a block).
        cpp = _recent_cpp(conn, adset_id)
        warn = None
        if cpp > 0 and (final_daily * 7 / cpp) < learn_thr:
            warn = f'~{final_daily*7/cpp:.0f} purchases/wk < {learn_thr:.0f} learning threshold'

        try:
            resp = meta.update_adset_daily_budget(adset_id, int(round(final_daily * MINOR)))
            conn.execute(text("""
                UPDATE aa_change_request
                SET status = 'pushed', pushed_at = NOW(), to_amount = :final,
                    from_amount = COALESCE(from_amount, :cur),
                    meta_response = CAST(:resp AS jsonb)
                WHERE id = :id
            """), {'final': round(final_daily, 2), 'cur': round(current_daily, 2),
                   'resp': json.dumps({'ok': True, 'clamped': was_clamped,
                                       'warning': warn, 'meta': resp}), 'id': cr_id})
            conn.commit()
            results.append({'id': cr_id, 'status': 'pushed', 'final_daily': round(final_daily, 2),
                            'clamped': was_clamped, 'warning': warn})
        except Exception as e:  # noqa: BLE001
            _fail(conn, cr_id, f'Meta write failed: {e}')
            results.append({'id': cr_id, 'status': 'failed', 'reason': 'write_error'})
    return results


def _fail(conn, cr_id, reason):
    conn.execute(text("""
        UPDATE aa_change_request SET status = 'failed',
            meta_response = CAST(:resp AS jsonb) WHERE id = :id
    """), {'resp': json.dumps({'ok': False, 'reason': reason}), 'id': cr_id})
    conn.commit()
