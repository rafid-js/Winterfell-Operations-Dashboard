"""Ad Allocator — allocation engine (pure functions, no I/O).

Implements the formulas (spec §7) and the 70/20/10 allocation logic with action
labels (spec §8). Everything here is deterministic and unit-tested; the data
layer feeds it plain dicts assembled from the brain tables, and the routes layer
persists what it returns.

Money is in whole BDT here — minor-unit conversion happens at the Meta boundary.
"""
from __future__ import annotations

COVER_CAP = 999.0  # reported when demand is ~zero (effectively infinite cover)


def _div(a, b, default=0.0):
    return (a / b) if b else default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ── Per-group derived metrics (§7) ───────────────────────────────────────────

def compute_group(m: dict, cfg: dict) -> dict:
    """Derive per-group metrics for the selected period.

    m carries raw aggregates assembled by the data layer:
      orders, units, revenue, spend            (selected period)
      period_days                              (length of the selected period)
      units_7d, units_3d                       (for trend + stock cover)
      spend_7d                                 (actual last-7-day spend, for gap)
      cpp_prev                                 (previous-period CPP, for drift)
      on_hand, in_stock, is_new, is_clearance
      cogs_pct                                 (group override or global)
    cfg carries the config constants (kept_rate, fixed_cost_per_day, ...).
    """
    orders = float(m.get('orders') or 0)
    revenue = float(m.get('revenue') or 0)
    spend = float(m.get('spend') or 0)
    period_days = float(m.get('period_days') or 1) or 1

    kept = float(cfg['kept_rate'])
    cogs = float(m.get('cogs_pct', cfg['cogs_pct']))
    if m.get('is_clearance'):
        cogs = float(cfg['clearance_cogs_pct'])

    # A group with no mapped spend has no real CPP — never invent one or a
    # profit/action from it (§4). Break-even CPP is still meaningful (it needs
    # only AOV), so we keep that.
    has_spend = m.get('has_spend', True)

    cpp = _div(spend, orders)
    aov = _div(revenue, orders)
    margin_rate = kept * (1 - cogs)          # kept × (1 − cogs)

    break_even_cpp = 0.0 if m.get('is_clearance') else margin_rate * aov
    if not has_spend:
        profit_per_order = 0.0
        est_profit = 0.0
        est_profit_7d = 0.0
        cpp = 0.0
    else:
        # Clearance: no product margin, every taka of CPP is pure loss (§7).
        profit_per_order = (-cpp) if m.get('is_clearance') else (margin_rate * aov - cpp)
        est_profit = orders * profit_per_order
        orders_per_day = _div(orders, period_days)
        est_profit_7d = profit_per_order * orders_per_day * 7

    # Stock cover with a short-term trend multiplier (clamped 0.5–1.5).
    units_7d = float(m.get('units_7d') or 0)
    units_3d = float(m.get('units_3d') or 0)
    run_rate_day = _div(units_7d, 7)
    if run_rate_day > 0:
        trend = _clamp(_div(units_3d, 3) / run_rate_day if run_rate_day else 1.0, 0.5, 1.5)
        cover_days = _div(float(m.get('on_hand') or 0), run_rate_day * trend, COVER_CAP)
    else:
        trend = 1.0
        cover_days = COVER_CAP

    cpp_prev = float(m.get('cpp_prev') or 0)
    cpp_drift = (_div(cpp, cpp_prev) - 1) if (has_spend and cpp_prev > 0) else 0.0

    return {
        'orders': orders, 'units': float(m.get('units') or 0), 'revenue': revenue,
        'spend': spend, 'cpp': cpp, 'aov': aov, 'has_spend': has_spend,
        'profit_per_order': profit_per_order, 'est_profit': est_profit,
        'est_profit_7d': est_profit_7d, 'break_even_cpp': break_even_cpp,
        'cover_days': min(cover_days, COVER_CAP), 'trend': trend,
        'cpp_drift': cpp_drift, 'spend_7d': float(m.get('spend_7d') or 0),
    }


def net_after_fixed(group_metrics: list[dict], cfg: dict, days: float) -> float:
    """Σ est_profit − fixed_cost_per_day × days (§7)."""
    total = sum(g['est_profit'] for g in group_metrics)
    return total - float(cfg['fixed_cost_per_day']) * float(days)


# ── Action labels (§8, first match wins) ─────────────────────────────────────

def action_label(g: dict, cfg: dict) -> tuple[str, str]:
    cover = g['cover_days']
    drift = g['cpp_drift']
    gap = g.get('gap', 0.0)
    gap_thr = float(cfg['gap_threshold'])

    # No mapped spend → mapping is the action; never an increase/profit verdict
    # (§4). New products are exempt: they belong in the test pool regardless.
    if not g.get('has_spend', True) and not g.get('is_new'):
        return 'needs_mapping', 'No spend mapped — map ads to this group'
    if not g.get('in_stock', True) or float(g.get('on_hand') or 0) <= 0:
        return 'pause', 'Out of stock'
    if g.get('is_clearance'):
        return 'cap_stop', 'Clearance — cap or stop'
    if cover < float(cfg['cover_low_days']):
        return 'low_stock', f'Low stock ({cover:.0f}d cover) — cap and reorder'
    if cover < float(cfg['cover_reorder_days']):
        return 'reorder', f'Reorder now ({cover:.0f}d cover, {int(cfg["lead_time_days"])}d lead)'
    if drift > float(cfg['drift_threshold']):
        return 'hold', f'CPP drift +{drift*100:.0f}% — hold'
    if g.get('is_new'):
        return 'test', 'New product — test budget'
    if gap > gap_thr:
        return 'increase', f'Under-funded by ৳{gap:,.0f} — increase'
    if gap < -gap_thr:
        return 'decrease', f'Over-funded by ৳{-gap:,.0f} — decrease'
    return 'hold', 'On target — hold'


# ── Allocation (§8, next 7 days) ─────────────────────────────────────────────

def allocate(groups: list[dict], cfg: dict) -> dict:
    """Assign next-7-day budget across groups.

    Each group dict must carry the compute_group() output plus flags
    (in_stock, is_new, is_clearance, on_hand) and identifiers the caller needs
    echoed back (id, name). Returns {'total', 'pools', 'rows'} where each row
    has rec_share, rec_amount, gap, action, reason.
    """
    total = float(cfg['weekly_budget'])
    proven_pool = total * float(cfg['proven_pool_pct'])
    test_pool = total * float(cfg['test_pool_pct'])
    retarget_pool = total * float(cfg['retargeting_pool_pct'])

    # Proven pool: mapped spend, in stock, not clearance, not new, positive
    # forward profit. No-spend groups are excluded (§4).
    proven = [g for g in groups if g.get('has_spend', True)
              and g.get('in_stock', True) and float(g.get('on_hand') or 0) > 0
              and not g.get('is_clearance') and not g.get('is_new')
              and g['est_profit_7d'] > 0]
    weights = {}
    for g in proven:
        stock_mult = 0.6 if g['cover_days'] < float(cfg['cover_reorder_days']) else 1.0
        drift_mult = 0.8 if g['cpp_drift'] > float(cfg['drift_threshold']) else 1.0
        weights[id(g)] = g['est_profit_7d'] * stock_mult * drift_mult
    weight_sum = sum(weights.values())

    # Test pool: new, in-stock products split equally.
    test = [g for g in groups if g.get('is_new') and g.get('in_stock', True)
            and float(g.get('on_hand') or 0) > 0]

    rows = []
    for g in groups:
        rec_amount = 0.0
        if id(g) in weights and weight_sum > 0:
            rec_amount = proven_pool * (weights[id(g)] / weight_sum)
        elif g in test and len(test) > 0:
            rec_amount = test_pool / len(test)

        gap = rec_amount - g.get('spend_7d', 0.0)
        g_for_action = {**g, 'gap': gap}
        action, reason = action_label(g_for_action, cfg)
        # Paused / stopped / unmapped groups get no budget regardless of pool math.
        if action in ('pause', 'cap_stop', 'needs_mapping'):
            rec_amount = 0.0
            gap = rec_amount - g.get('spend_7d', 0.0)

        rec_share = _div(rec_amount, total)
        rows.append({**g, 'rec_amount': round(rec_amount, 2), 'rec_share': rec_share,
                     'gap': round(gap, 2), 'action': action, 'reason': reason})

    # Rank by est_profit_7d (desc) for display.
    rows.sort(key=lambda r: r['est_profit_7d'], reverse=True)
    for i, r in enumerate(rows, 1):
        r['rank'] = i

    return {
        'total': total,
        'pools': {'proven': round(proven_pool, 2), 'test': round(test_pool, 2),
                  'retargeting': round(retarget_pool, 2)},
        'rows': rows,
    }
