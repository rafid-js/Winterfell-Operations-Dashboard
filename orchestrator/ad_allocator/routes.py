"""Ad Allocator — Flask blueprint: dashboard page + JSON API.

Pattern matches the other blueprints (render_template_string + /api/* routes,
own DB connection per request via get_connection).
"""
import os
import sys

from flask import request, jsonify, render_template_string

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'brain'))
from db import get_connection  # noqa: E402

from . import aa_bp, engine, data, config_store, mapping, runs, meta_push  # noqa: E402
from .ui import PAGE_HTML  # noqa: E402


# ── Overview (KPIs + groups + allocation) ────────────────────────────────────

def _build_overview(conn, period, mode, cfg):
    if mode == 'sample':
        assembled = data.assemble_sample(cfg)
    else:
        assembled = data.assemble_live(conn, period, cfg)
    groups = assembled['groups']
    alloc = engine.allocate(groups, cfg)

    days = assembled['days']
    unmapped = float(assembled['unmapped_spend'])
    mapped_spend = float(assembled.get('mapped_spend', sum(g['spend'] for g in groups)))
    # Spend reconciles exactly: mapped (group rows) + unmapped = total Meta spend.
    total_spend = float(assembled.get('total_ad_spend', mapped_spend + unmapped))

    # Orders / revenue stay ACCOUNT-WIDE (all counted orders) so the headline is
    # correct regardless of how complete the ad→group mapping is.
    acct = assembled.get('account') or {}
    orders = int(acct.get('orders', sum(g['orders'] for g in groups)))
    units = int(acct.get('units', sum(g['units'] for g in groups)))
    revenue = float(acct.get('revenue', sum(g['revenue'] for g in groups)))

    margin_rate = float(cfg['kept_rate']) * (1 - float(cfg['cogs_pct']))
    gross_margin = margin_rate * revenue
    est_profit = gross_margin - total_spend                       # after ads
    after_fixed = est_profit - float(cfg['fixed_cost_per_day']) * days

    kpis = {
        'ad_spend': round(total_spend, 2), 'mapped_spend': round(mapped_spend, 2),
        'orders': orders, 'units': units, 'revenue': round(revenue, 2),
        'blended_cpp': round(engine._div(total_spend, orders), 2),
        'roas': round(engine._div(revenue, total_spend), 2),
        'est_profit': round(est_profit, 2),
        'after_fixed': round(after_fixed, 2),
        'unmapped_spend': round(unmapped, 2),
    }
    return {'kpis': kpis, 'allocation': alloc, 'pools': alloc['pools'],
            'rows': alloc['rows'], 'days': days, 'mode': mode, 'period': period,
            'unmapped_spend': round(unmapped, 2), 'mapped_spend': round(mapped_spend, 2),
            'total_ad_spend': round(total_spend, 2),
            'since': assembled['since'], 'until': assembled['until']}


@aa_bp.route('/ad-allocator')
def ad_allocator_page():
    return render_template_string(PAGE_HTML)


@aa_bp.route('/api/ad-allocator/overview')
def api_overview():
    period = request.args.get('period', '7d')
    mode = request.args.get('mode', 'live')
    with get_connection() as conn:
        cfg = config_store.load_config(conn)
        # Fall back to sample automatically if live has no mapped groups/data.
        try:
            ov = _build_overview(conn, period, mode, cfg)
            if mode == 'live' and not ov['rows']:
                ov = _build_overview(conn, period, 'sample', cfg)
                ov['auto_sample'] = True
        except Exception as e:  # noqa: BLE001
            print(f'  ⚠ overview live failed, using sample: {e}', flush=True)
            ov = _build_overview(conn, period, 'sample', cfg)
            ov['auto_sample'] = True
    return jsonify(ov)


# ── Config ───────────────────────────────────────────────────────────────────

@aa_bp.route('/api/ad-allocator/config', methods=['GET', 'POST'])
def api_config():
    with get_connection() as conn:
        if request.method == 'POST':
            body = request.get_json(force=True) or {}
            config_store.set_config(conn, body['key'], float(body['value']))
        return jsonify(config_store.load_config(conn))


@aa_bp.route('/api/ad-allocator/status-map', methods=['GET', 'POST'])
def api_status_map():
    with get_connection() as conn:
        if request.method == 'POST':
            body = request.get_json(force=True) or {}
            config_store.set_status_bucket(conn, body['slug'], body['bucket'])
        return jsonify(config_store.status_map(conn))


# ── Mapping admin ────────────────────────────────────────────────────────────

@aa_bp.route('/api/ad-allocator/groups', methods=['GET', 'POST'])
def api_groups():
    with get_connection() as conn:
        if request.method == 'POST':
            b = request.get_json(force=True) or {}
            gid = mapping.create_group(
                conn, b['name'], b.get('category'), b.get('is_new', False),
                b.get('is_clearance', False), b.get('cogs_pct'), b.get('aov_override'))
            return jsonify({'id': gid})
        return jsonify(mapping.list_groups(conn))


@aa_bp.route('/api/ad-allocator/groups/<int:gid>', methods=['DELETE'])
def api_group_delete(gid):
    with get_connection() as conn:
        mapping.delete_group(conn, gid)
    return jsonify({'deleted': gid})


@aa_bp.route('/api/ad-allocator/maps', methods=['GET', 'POST'])
def api_maps():
    with get_connection() as conn:
        if request.method == 'POST':
            b = request.get_json(force=True) or {}
            mapping.add_map(conn, b['product_group_id'], b.get('meta_adset_id'),
                            b.get('meta_campaign_id'), b.get('meta_ad_id'),
                            float(b.get('weight', 1.0)))
            return jsonify({'ok': True})
        return jsonify(mapping.list_maps(conn))


@aa_bp.route('/api/ad-allocator/maps/<int:map_id>', methods=['DELETE'])
def api_map_delete(map_id):
    with get_connection() as conn:
        mapping.delete_map(conn, map_id)
    return jsonify({'deleted': map_id})


@aa_bp.route('/api/ad-allocator/bootstrap-groups', methods=['POST'])
def api_bootstrap_groups():
    mode = (request.get_json(silent=True) or {}).get('mode', 'product')
    with get_connection() as conn:
        res = mapping.bootstrap_from_catalog(conn, mode)
    return jsonify({**res, 'mode': mode})


@aa_bp.route('/api/ad-allocator/sync-ads', methods=['POST'])
def api_sync_ads():
    """Sync per-product Meta spend (catalog product_id breakdown) — the primary
    attribution source. Name kept for the existing UI button."""
    days = (request.get_json(silent=True) or {}).get('days', 30)
    from . import product_sync
    try:
        return jsonify(product_sync.run(days))
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': str(e)}), 500


@aa_bp.route('/api/ad-allocator/ads')
def api_ads():
    period = request.args.get('period', '30d')
    with get_connection() as conn:
        cfg = config_store.load_config(conn)
        return jsonify(data.ads_view(conn, period, cfg))


@aa_bp.route('/api/ad-allocator/unmapped-ads')
def api_unmapped_ads():
    period = request.args.get('period', '30d')
    since, until, _ = data.period_bounds(period)
    with get_connection() as conn:
        return jsonify(mapping.unmapped_ads(conn, since, until))


@aa_bp.route('/api/ad-allocator/suggest-ads')
def api_suggest_ads():
    period = request.args.get('period', '30d')
    since, until, _ = data.period_bounds(period)
    with get_connection() as conn:
        return jsonify(mapping.suggest_ads(conn, since, until))


@aa_bp.route('/api/ad-allocator/suggest')
def api_suggest():
    with get_connection() as conn:
        return jsonify(mapping.suggest(conn))


@aa_bp.route('/api/ad-allocator/timeseries')
def api_timeseries():
    months = request.args.get('months', 7, type=int)
    with get_connection() as conn:
        counted = config_store.counted_slugs(conn)
        try:
            return jsonify(data.monthly_series(conn, counted, months))
        except Exception as e:  # noqa: BLE001
            print(f'  ⚠ timeseries failed: {e}', flush=True)
            return jsonify([])


@aa_bp.route('/api/ad-allocator/unmapped-adsets')
def api_unmapped_adsets():
    period = request.args.get('period', '30d')
    since, until, _ = data.period_bounds(period)
    with get_connection() as conn:
        return jsonify(mapping.unmapped_adsets(conn, since, until))


# ── Runs ─────────────────────────────────────────────────────────────────────

@aa_bp.route('/api/ad-allocator/run', methods=['POST'])
def api_run():
    period = (request.get_json(silent=True) or {}).get('period', '7d')
    with get_connection() as conn:
        cfg = config_store.load_config(conn)
        ov = _build_overview(conn, period, 'live', cfg)
        run_id = runs.create_run(conn, period, cfg, ov['allocation'])
    return jsonify({'run_id': run_id})


@aa_bp.route('/api/ad-allocator/runs')
def api_runs():
    with get_connection() as conn:
        return jsonify(runs.list_runs(conn))


@aa_bp.route('/api/ad-allocator/runs/<int:run_id>')
def api_run_get(run_id):
    with get_connection() as conn:
        return jsonify(runs.get_run(conn, run_id) or {})


# ── Change requests / push (phase 3) ─────────────────────────────────────────

@aa_bp.route('/api/ad-allocator/runs/<int:run_id>/build-changes', methods=['POST'])
def api_build_changes(run_id):
    with get_connection() as conn:
        n = meta_push.build_change_requests(conn, run_id)
    return jsonify({'created': n})


@aa_bp.route('/api/ad-allocator/changes')
def api_changes():
    status = request.args.get('status')
    run_id = request.args.get('run_id', type=int)
    with get_connection() as conn:
        return jsonify(meta_push.list_change_requests(conn, run_id, status))


@aa_bp.route('/api/ad-allocator/changes/<int:cr_id>/<action>', methods=['POST'])
def api_change_action(cr_id, action):
    if action not in ('approve', 'reject'):
        return jsonify({'error': 'bad action'}), 400
    who = (request.get_json(silent=True) or {}).get('who', 'dashboard')
    with get_connection() as conn:
        meta_push.set_status(conn, cr_id, 'approved' if action == 'approve' else 'rejected', who)
    return jsonify({'ok': True})


@aa_bp.route('/api/ad-allocator/changes/push', methods=['POST'])
def api_push():
    with get_connection() as conn:
        results = meta_push.push_approved(conn)
    return jsonify({'results': results})
