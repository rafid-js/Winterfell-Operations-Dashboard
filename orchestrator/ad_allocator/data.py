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


# ── Orders & stock rolled up SKU -> group via aa_sku_group_map ────────────────

def _orders_by_group(conn, since, until, counted):
    """{group_id: {orders, units, revenue}} from counted order lines, rolled up
    SKU -> group. A SKU belongs to exactly one group, so no overlap/mixing."""
    rows = conn.execute(text("""
        SELECT m.product_group_id            AS gid,
               COUNT(*)                       AS lines,
               SUM(COALESCE(oi.quantity,0))   AS units,
               SUM(COALESCE(oi.total_price,0))AS revenue
        FROM order_items oi
        JOIN orders o ON oi.so_number = o.so_number
        JOIN aa_sku_group_map m ON m.sku = oi.sku
        WHERE o.order_date::date BETWEEN :since AND :until
          AND lower(COALESCE(o.wc_status, o.nuport_status, '')) = ANY(:counted)
        GROUP BY m.product_group_id
    """), {'since': since, 'until': until,
           'counted': [s.lower() for s in counted]}).fetchall()
    return {r._mapping['gid']: {'orders': int(r._mapping['lines'] or 0),
                                'units': float(r._mapping['units'] or 0),
                                'revenue': float(r._mapping['revenue'] or 0)} for r in rows}


def _stock_by_group(conn):
    rows = conn.execute(text("""
        SELECT m.product_group_id AS gid, SUM(COALESCE(s.current_stock,0)) AS on_hand
        FROM skus s JOIN aa_sku_group_map m ON m.sku = s.sku
        WHERE s.is_active = TRUE
        GROUP BY m.product_group_id
    """)).fetchall()
    return {r._mapping['gid']: float(r._mapping['on_hand'] or 0) for r in rows}


# ── Spend attribution: per-AD (preferred) with adset-level fallback ──────────

def _ad_spend_rows(conn, since, until):
    """Per-ad spend rows from aa_meta_ad_daily if synced, else None."""
    rows = conn.execute(text("""
        SELECT ad_id, adset_id, campaign_id, SUM(COALESCE(spend_bdt,0)) AS spend
        FROM aa_meta_ad_daily
        WHERE account_id = :acct AND date BETWEEN :since AND :until
        GROUP BY ad_id, adset_id, campaign_id
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchall()
    return [dict(r._mapping) for r in rows] if rows else None


def _attribute_spend(groups, mapping, ad_rows, adset_rows):
    """Attribute spend to groups. Prefer per-ad rows (ad_id -> group), falling
    back to adset-level rows when the ad-level table isn't synced. Returns
    (spend_by_group, mapped_total, total_spend)."""
    out = {g['id']: 0.0 for g in groups}
    by_ad, by_adset, by_campaign = {}, {}, {}
    for mp in mapping:
        w = float(mp.get('weight') or 1)
        if mp.get('meta_ad_id'):
            by_ad.setdefault(str(mp['meta_ad_id']), []).append((mp['product_group_id'], w))
        if mp.get('meta_adset_id'):
            by_adset.setdefault(str(mp['meta_adset_id']), []).append((mp['product_group_id'], w))
        if mp.get('meta_campaign_id'):
            by_campaign.setdefault(str(mp['meta_campaign_id']), []).append((mp['product_group_id'], w))

    rows = ad_rows if ad_rows is not None else adset_rows
    total = mapped = 0.0
    for sr in rows:
        spend = float(sr['spend'] or 0)
        total += spend
        hits = (by_ad.get(str(sr.get('ad_id'))) if ad_rows is not None else None) \
            or by_adset.get(str(sr.get('adset_id'))) \
            or by_campaign.get(str(sr.get('campaign_id')))
        if hits:
            for gid, w in hits:
                if gid in out:
                    out[gid] += spend * w
                    mapped += spend * w
    return out, mapped, total


def account_totals(conn, since, until, counted):
    """Account-wide totals for the KPI row — all counted orders and all RAGNAR
    spend in the window, independent of group mapping coverage."""
    o = conn.execute(text("""
        SELECT COUNT(*) AS lines, SUM(COALESCE(oi.quantity,0)) AS units,
               SUM(COALESCE(oi.total_price,0)) AS revenue
        FROM order_items oi JOIN orders o ON oi.so_number = o.so_number
        WHERE o.order_date::date BETWEEN :since AND :until
          AND lower(COALESCE(o.wc_status, o.nuport_status, '')) = ANY(:counted)
    """), {'since': since, 'until': until,
           'counted': [s.lower() for s in counted]}).fetchone()
    s = conn.execute(text("""
        SELECT SUM(COALESCE(spend_bdt,0)) AS spend FROM ad_spend
        WHERE account_id = :acct AND date BETWEEN :since AND :until
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchone()
    m = o._mapping
    return {'orders': int(m['lines'] or 0), 'units': float(m['units'] or 0),
            'revenue': float(m['revenue'] or 0),
            'total_spend': float(s._mapping['spend'] or 0)}


def monthly_series(conn, counted, months=7):
    """Monthly account-wide ROAS and cost-per-conversion for the trend charts.

    ROAS = counted-order revenue / RAGNAR spend; CPC (cost per conversion) =
    spend / orders. Returns a continuous list of the last `months` months.
    """
    spend = conn.execute(text("""
        SELECT to_char(date_trunc('month', date), 'YYYY-MM') AS m,
               SUM(COALESCE(spend_bdt,0)) AS spend
        FROM ad_spend
        WHERE account_id = :acct AND date >= (date_trunc('month', CURRENT_DATE)
              - ((:months - 1) || ' months')::interval)
        GROUP BY 1
    """), {'acct': RAGNAR_ACCOUNT_ID, 'months': months}).fetchall()
    orders = conn.execute(text("""
        SELECT to_char(date_trunc('month', o.order_date), 'YYYY-MM') AS m,
               COUNT(*) AS orders, SUM(COALESCE(oi.total_price,0)) AS revenue
        FROM order_items oi JOIN orders o ON oi.so_number = o.so_number
        WHERE o.order_date >= (date_trunc('month', CURRENT_DATE)
              - ((:months - 1) || ' months')::interval)
          AND lower(COALESCE(o.wc_status, o.nuport_status, '')) = ANY(:counted)
        GROUP BY 1
    """), {'months': months, 'counted': [s.lower() for s in counted]}).fetchall()

    s_by = {r._mapping['m']: float(r._mapping['spend'] or 0) for r in spend}
    o_by = {r._mapping['m']: (int(r._mapping['orders'] or 0), float(r._mapping['revenue'] or 0))
            for r in orders}

    today = date.today()
    keys = []
    y, mo = today.year, today.month
    for _ in range(months):
        keys.append(f'{y:04d}-{mo:02d}')
        mo -= 1
        if mo == 0:
            mo = 12; y -= 1
    keys.reverse()

    out = []
    for k in keys:
        sp = s_by.get(k, 0.0)
        od, rev = o_by.get(k, (0, 0.0))
        out.append({'month': k, 'spend': round(sp, 2), 'orders': od,
                    'revenue': round(rev, 2),
                    'roas': round(rev / sp, 2) if sp else 0.0,
                    'cpc': round(sp / od, 2) if od else 0.0})
    return out


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

    # Orders rolled up SKU -> group (never by name match).
    ord_p = _orders_by_group(conn, since, until, counted)
    ord_7 = _orders_by_group(conn, w_since, until, counted)
    ord_3 = _orders_by_group(conn, d_since, until, counted)
    ord_pr = _orders_by_group(conn, p_since, p_until, counted)
    stock = _stock_by_group(conn)

    # Spend attributed per-ad (preferred) with adset-level fallback.
    spend_by_g, mapped_total, total_spend = _attribute_spend(
        groups, mapping, _ad_spend_rows(conn, since, until), _spend_by_key(conn, since, until))
    spend7_by_g, _, _ = _attribute_spend(
        groups, mapping, _ad_spend_rows(conn, w_since, until), _spend_by_key(conn, w_since, until))
    spendp_by_g, _, _ = _attribute_spend(
        groups, mapping, _ad_spend_rows(conn, p_since, p_until), _spend_by_key(conn, p_since, p_until))

    metrics = []
    for g in groups:
        gid = g['id']
        o = ord_p.get(gid, {'orders': 0, 'units': 0.0, 'revenue': 0.0})
        opr = ord_pr.get(gid, {'orders': 0})
        g_spend = spend_by_g.get(gid, 0.0)
        has_spend = g_spend > 0
        cpp_prev = engine._div(spendp_by_g.get(gid, 0.0), opr['orders']) if opr['orders'] else 0
        raw = {
            'orders': o['orders'], 'units': o['units'], 'revenue': o['revenue'],
            'spend': g_spend, 'period_days': days,
            'units_7d': ord_7.get(gid, {}).get('units', 0.0),
            'units_3d': ord_3.get(gid, {}).get('units', 0.0),
            'spend_7d': spend7_by_g.get(gid, 0.0), 'cpp_prev': cpp_prev,
            'on_hand': stock.get(gid, 0.0), 'in_stock': stock.get(gid, 0.0) > 0,
            'is_new': bool(g['is_new']), 'is_clearance': bool(g['is_clearance']),
            'has_spend': has_spend,
            'cogs_pct': float(g['cogs_pct']) if g['cogs_pct'] is not None else cfg['cogs_pct'],
        }
        d = engine.compute_group(raw, cfg)
        d.update({'id': gid, 'name': g['name'], 'category': g['category'],
                  'in_stock': raw['in_stock'], 'is_new': raw['is_new'],
                  'is_clearance': raw['is_clearance'], 'on_hand': raw['on_hand'],
                  'has_spend': has_spend})
        metrics.append(d)

    unmapped_spend = max(0.0, total_spend - mapped_total)
    return {'groups': metrics, 'days': days,
            'unmapped_spend': round(unmapped_spend, 2),
            'mapped_spend': round(mapped_total, 2),
            'total_ad_spend': round(total_spend, 2),
            'account': account_totals(conn, since, until, counted),
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
               'on_hand': on_hand, 'in_stock': on_hand > 0, 'has_spend': True,
               'is_new': is_new, 'is_clearance': is_clr, 'cogs_pct': cfg['cogs_pct']}
        d = engine.compute_group(raw, cfg)
        d.update({'id': i, 'name': name, 'category': 'Sample',
                  'in_stock': on_hand > 0, 'is_new': is_new,
                  'is_clearance': is_clr, 'on_hand': on_hand, 'has_spend': True})
        metrics.append(d)
    acct = {'orders': sum(g['orders'] for g in metrics),
            'units': sum(g['units'] for g in metrics),
            'revenue': sum(g['revenue'] for g in metrics),
            'total_spend': sum(g['spend'] for g in metrics) + 4200.0}
    return {'groups': metrics, 'days': 7, 'unmapped_spend': 4200.0,
            'mapped_spend': round(sum(g['spend'] for g in metrics), 2),
            'total_ad_spend': round(acct['total_spend'], 2),
            'account': acct, 'since': 'sample', 'until': 'sample'}


# ── Ads tab: per-ad view with verdict ────────────────────────────────────────

def ads_view(conn, period, cfg):
    """Per-ad rows: spend, Meta purchases, ad CPP, mapped group, that group's
    break-even CPP, and a verdict (scale / cut / wait)."""
    since, until, days = period_bounds(period)
    counted = config_store.counted_slugs(conn)
    min_days = int(cfg['min_days_data'])

    # ad -> group from the mapping (ad level only here)
    ad_map = {str(r._mapping['meta_ad_id']): r._mapping['product_group_id']
              for r in conn.execute(text(
                  "SELECT meta_ad_id, product_group_id FROM aa_ad_product_map "
                  "WHERE meta_ad_id IS NOT NULL")).fetchall()}

    # group break-even CPP = 0.275 × AOV (clearance 0), from mapped orders
    ord_g = _orders_by_group(conn, since, until, counted)
    gmeta = {r._mapping['id']: dict(r._mapping) for r in conn.execute(text(
        "SELECT id, name, is_clearance FROM aa_product_group")).fetchall()}
    margin_rate = float(cfg['kept_rate']) * (1 - float(cfg['cogs_pct']))
    be_by_group = {}
    for gid, o in ord_g.items():
        aov = (o['revenue'] / o['orders']) if o['orders'] else 0
        be = 0.0 if (gmeta.get(gid) or {}).get('is_clearance') else margin_rate * aov
        be_by_group[gid] = be

    rows = conn.execute(text("""
        SELECT ad_id, MAX(ad_name) AS ad_name, MAX(adset_name) AS adset_name,
               SUM(COALESCE(spend_bdt,0)) AS spend, SUM(COALESCE(purchases,0)) AS purchases,
               COUNT(DISTINCT date) AS ndays
        FROM aa_meta_ad_daily
        WHERE account_id = :acct AND date BETWEEN :since AND :until
        GROUP BY ad_id ORDER BY spend DESC
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchall()

    out = []
    for r in rows:
        m = r._mapping
        spend, purch = float(m['spend'] or 0), int(m['purchases'] or 0)
        gid = ad_map.get(str(m['ad_id']))
        cpp = (spend / purch) if purch else None
        be = be_by_group.get(gid)
        if int(m['ndays'] or 0) < min_days:
            verdict = 'wait'
        elif cpp is None:
            verdict = 'cut' if spend > 0 else 'wait'
        elif be and cpp <= be:
            verdict = 'scale'
        elif be and cpp > be:
            verdict = 'cut'
        else:
            verdict = 'review'
        out.append({'ad_id': m['ad_id'], 'ad_name': m['ad_name'], 'adset_name': m['adset_name'],
                    'spend': round(spend, 2), 'purchases': purch,
                    'cpp': round(cpp, 2) if cpp is not None else None,
                    'group_id': gid, 'group_name': (gmeta.get(gid) or {}).get('name'),
                    'break_even_cpp': round(be, 2) if be else None,
                    'ndays': int(m['ndays'] or 0), 'verdict': verdict})
    return {'ads': out, 'since': since.isoformat(), 'until': until.isoformat()}
