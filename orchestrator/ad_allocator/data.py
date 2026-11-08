"""Ad Allocator — data assembly from the live brain tables.

Pulls spend from ad_spend, orders/units/revenue from order_items+orders, and
stock from skus, attributes them to product groups, and hands plain dicts to the
engine. Also provides a SAMPLE generator so the dashboard renders before any
mapping exists.

Attribution notes (v1, documented deviations kept deliberately simple):
  • Orders are counted at order-LINE grain (one order_items row ≈ one order).
    Fashion AOV is 1–2 items, so this holds for ranking; can be refined to
    distinct-order grain later.
  • A product is attributed to a group when the group name appears in the
    product name (case-insensitive). Name groups distinctly to avoid overlap.
  • Only the RAGNAR account (BDT) is read. IVAR (USD) is the phase-4 switcher.
"""
from datetime import date, timedelta

from sqlalchemy import text

from . import engine, config_store

RAGNAR_ACCOUNT_ID = 'act_726228332038123'

PERIODS = {'7d': 7, '15d': 15, '30d': 30, '6m': 182, '1y': 365}


def period_bounds(period: str):
    days = PERIODS.get(period, 7)
    until = date.today()                 # include today's partial day (§2)
    since = until - timedelta(days=days - 1)
    return since, until, days


# ── Live assembly ────────────────────────────────────────────────────────────

def _spend_by_key(conn, since, until):
    rows = conn.execute(text("""
        SELECT adset_id, campaign_id,
               SUM(COALESCE(spend_bdt,0))          AS spend,
               SUM(COALESCE(orders_attributed,0))  AS oa,
               SUM(COALESCE(revenue_attributed,0)) AS rev
        FROM ad_spend
        WHERE account_id = :acct AND date BETWEEN :since AND :until
        GROUP BY adset_id, campaign_id
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchall()
    return [dict(r._mapping) for r in rows]


def _product_rows(conn, since, until, counted):
    rows = conn.execute(text("""
        SELECT oi.product_name                       AS product_name,
               COUNT(*)                              AS lines,
               SUM(COALESCE(oi.quantity,0))          AS units,
               SUM(COALESCE(oi.total_price,0))       AS revenue
        FROM order_items oi
        JOIN orders o ON oi.so_number = o.so_number
        WHERE o.order_date::date BETWEEN :since AND :until
          AND lower(COALESCE(o.wc_status, o.nuport_status, '')) = ANY(:counted)
          AND oi.product_name IS NOT NULL
        GROUP BY oi.product_name
    """), {'since': since, 'until': until,
           'counted': [s.lower() for s in counted]}).fetchall()
    return [dict(r._mapping) for r in rows]


def _stock_rows(conn):
    rows = conn.execute(text("""
        SELECT product_name, SUM(COALESCE(current_stock,0)) AS on_hand
        FROM skus WHERE is_active = TRUE AND product_name IS NOT NULL
        GROUP BY product_name
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


def _attribute_products(groups, product_rows):
    """Sum line/unit/revenue per group by case-insensitive name containment."""
    out = {g['id']: {'orders': 0, 'units': 0.0, 'revenue': 0.0} for g in groups}
    lowered = [(g['id'], g['name'].lower()) for g in groups]
    for pr in product_rows:
        pname = (pr['product_name'] or '').lower()
        for gid, gname in lowered:
            if gname and gname in pname:
                out[gid]['orders'] += int(pr['lines'] or 0)
                out[gid]['units'] += float(pr['units'] or 0)
                out[gid]['revenue'] += float(pr['revenue'] or 0)
    return out


def _attribute_stock(groups, stock_rows):
    out = {g['id']: 0.0 for g in groups}
    lowered = [(g['id'], g['name'].lower()) for g in groups]
    for sr in stock_rows:
        pname = (sr['product_name'] or '').lower()
        for gid, gname in lowered:
            if gname and gname in pname:
                out[gid] += float(sr['on_hand'] or 0)
    return out


def _attribute_spend(groups, mapping, spend_rows):
    """Attribute each spend row to a group via adset match, else campaign, × weight."""
    out = {g['id']: 0.0 for g in groups}
    by_adset, by_campaign = {}, {}
    for mp in mapping:
        if mp.get('meta_adset_id'):
            by_adset[str(mp['meta_adset_id'])] = (mp['product_group_id'], float(mp['weight'] or 1))
        if mp.get('meta_campaign_id'):
            by_campaign[str(mp['meta_campaign_id'])] = (mp['product_group_id'], float(mp['weight'] or 1))
    mapped_total = 0.0
    for sr in spend_rows:
        spend = float(sr['spend'] or 0)
        key_as, key_cp = str(sr.get('adset_id')), str(sr.get('campaign_id'))
        hit = by_adset.get(key_as) or by_campaign.get(key_cp)
        if hit:
            gid, weight = hit
            if gid in out:
                out[gid] += spend * weight
                mapped_total += spend * weight
    return out, mapped_total


def assemble_live(conn, period, cfg, status_filter=None):
    since, until, days = period_bounds(period)
    p_since, p_until = since - timedelta(days=days), since - timedelta(days=1)  # prev period
    w_since = until - timedelta(days=6)                                         # last 7d
    d_since = until - timedelta(days=2)                                         # last 3d
    counted = config_store.counted_slugs(conn)

    groups = [dict(r._mapping) for r in conn.execute(text(
        "SELECT * FROM aa_product_group ORDER BY name")).fetchall()]
    mapping = [dict(r._mapping) for r in conn.execute(text(
        "SELECT * FROM aa_ad_product_map")).fetchall()]

    spend_rows = _spend_by_key(conn, since, until)
    prod = _attribute_products(groups, _product_rows(conn, since, until, counted))
    prod7 = _attribute_products(groups, _product_rows(conn, w_since, until, counted))
    prod3 = _attribute_products(groups, _product_rows(conn, d_since, until, counted))
    prodp = _attribute_products(groups, _product_rows(conn, p_since, p_until, counted))
    stock = _attribute_stock(groups, _stock_rows(conn))
    spend_by_g, mapped_total = _attribute_spend(groups, mapping, spend_rows)
    spend7_by_g, _ = _attribute_spend(groups, mapping, _spend_by_key(conn, w_since, until))
    spendp_by_g, _ = _attribute_spend(groups, mapping, _spend_by_key(conn, p_since, p_until))

    metrics = []
    for g in groups:
        gid = g['id']
        prev_orders = prod[gid]['orders'] and prodp[gid]['orders']
        cpp_prev = engine._div(spendp_by_g[gid], prodp[gid]['orders']) if prodp[gid]['orders'] else 0
        raw = {
            'orders': prod[gid]['orders'], 'units': prod[gid]['units'],
            'revenue': prod[gid]['revenue'], 'spend': spend_by_g[gid],
            'period_days': days, 'units_7d': prod7[gid]['units'],
            'units_3d': prod3[gid]['units'], 'spend_7d': spend7_by_g[gid],
            'cpp_prev': cpp_prev, 'on_hand': stock[gid],
            'in_stock': stock[gid] > 0, 'is_new': bool(g['is_new']),
            'is_clearance': bool(g['is_clearance']),
            'cogs_pct': float(g['cogs_pct']) if g['cogs_pct'] is not None else cfg['cogs_pct'],
        }
        d = engine.compute_group(raw, cfg)
        d.update({'id': gid, 'name': g['name'], 'category': g['category'],
                  'in_stock': raw['in_stock'], 'is_new': raw['is_new'],
                  'is_clearance': raw['is_clearance'], 'on_hand': raw['on_hand']})
        metrics.append(d)

    total_spend = sum(float(s['spend'] or 0) for s in spend_rows)
    unmapped_spend = max(0.0, total_spend - mapped_total)
    return {'groups': metrics, 'days': days,
            'unmapped_spend': round(unmapped_spend, 2),
            'since': since.isoformat(), 'until': until.isoformat()}


# ── Sample generator (SAMPLE badge) ──────────────────────────────────────────

_SAMPLE = [
    # name, aov, orders, cogs_new_clear, on_hand, u7, u3, is_new, is_clr
    ('Cord Straight Fit Pant', 1450, 180, None, 820, 190, 95, False, False),
    ('Drop Shoulder Tee',       780, 240, None, 140, 160, 40, False, False),
    ('Cargo Pants',            1600, 120, None, 60,  90,  48, False, False),
    ('Denim Jacket',           2400,  70, None, 300, 55,  22, False, False),
    ('Oversized Hoodie',       1800,  40, None, 500, 45,  25, True,  False),
    ('Summer Clearance Shirt',  600,  90, None, 400, 70,  10, False, True),
]


def assemble_sample(cfg):
    metrics = []
    for i, (name, aov, orders, _c, on_hand, u7, u3, is_new, is_clr) in enumerate(_SAMPLE, 1):
        spend = aov * orders * 0.18                 # ~18% of revenue to ads
        raw = {'orders': orders, 'units': orders * 1.3, 'revenue': aov * orders,
               'spend': spend, 'period_days': 7, 'units_7d': u7, 'units_3d': u3,
               'spend_7d': spend, 'cpp_prev': spend / orders * 0.92,
               'on_hand': on_hand, 'in_stock': on_hand > 0,
               'is_new': is_new, 'is_clearance': is_clr, 'cogs_pct': cfg['cogs_pct']}
        d = engine.compute_group(raw, cfg)
        d.update({'id': i, 'name': name, 'category': 'Sample',
                  'in_stock': on_hand > 0, 'is_new': is_new,
                  'is_clearance': is_clr, 'on_hand': on_hand})
        metrics.append(d)
    return {'groups': metrics, 'days': 7, 'unmapped_spend': 4200.0,
            'since': 'sample', 'until': 'sample'}
