"""Ad Allocator — product groups + ad→group mapping (spec §5).

Ads target category groups, not SKUs. aa_ad_product_map links a Meta
ad/adset/campaign to a product group with a split weight. The suggester matches
group names against ad-set / campaign names to seed the manual admin screen.
"""
from sqlalchemy import text

from .data import RAGNAR_ACCOUNT_ID


# ── Product groups CRUD ──────────────────────────────────────────────────────

def list_groups(conn):
    rows = conn.execute(text("SELECT * FROM aa_product_group ORDER BY name")).fetchall()
    return [dict(r._mapping) for r in rows]


def create_group(conn, name, category=None, is_new=False, is_clearance=False,
                 cogs_pct=None, aov_override=None):
    gid = conn.execute(text("""
        INSERT INTO aa_product_group (name, category, is_new, is_clearance, cogs_pct, aov_override)
        VALUES (:n, :c, :nw, :cl, :cg, :ao)
        ON CONFLICT (name) DO UPDATE SET category = EXCLUDED.category,
            is_new = EXCLUDED.is_new, is_clearance = EXCLUDED.is_clearance,
            cogs_pct = EXCLUDED.cogs_pct, aov_override = EXCLUDED.aov_override,
            updated_at = NOW()
        RETURNING id
    """), {'n': name, 'c': category, 'nw': is_new, 'cl': is_clearance,
           'cg': cogs_pct, 'ao': aov_override}).scalar()
    conn.commit()
    return gid


def delete_group(conn, group_id):
    conn.execute(text("DELETE FROM aa_product_group WHERE id = :id"), {'id': group_id})
    conn.commit()


# ── Ad→group mapping CRUD ────────────────────────────────────────────────────

def list_maps(conn):
    rows = conn.execute(text("""
        SELECT m.*, g.name AS group_name
        FROM aa_ad_product_map m JOIN aa_product_group g ON m.product_group_id = g.id
        ORDER BY g.name
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


def add_map(conn, product_group_id, meta_adset_id=None, meta_campaign_id=None,
            meta_ad_id=None, weight=1.0):
    conn.execute(text("""
        INSERT INTO aa_ad_product_map
          (product_group_id, meta_adset_id, meta_campaign_id, meta_ad_id, weight)
        VALUES (:g, :a, :c, :ad, :w)
    """), {'g': product_group_id, 'a': meta_adset_id, 'c': meta_campaign_id,
           'ad': meta_ad_id, 'w': weight})
    conn.commit()


def delete_map(conn, map_id):
    conn.execute(text("DELETE FROM aa_ad_product_map WHERE id = :id"), {'id': map_id})
    conn.commit()


# ── SKU → group rollup ───────────────────────────────────────────────────────

def upsert_sku_map(conn, sku, product_group_id):
    conn.execute(text("""
        INSERT INTO aa_sku_group_map (sku, product_group_id, updated_at)
        VALUES (:s, :g, NOW())
        ON CONFLICT (sku) DO UPDATE SET product_group_id = :g, updated_at = NOW()
    """), {'s': sku, 'g': product_group_id})


def sku_map_count(conn):
    return conn.execute(text("SELECT COUNT(*) FROM aa_sku_group_map")).scalar() or 0


# ── Bootstrap groups + SKU rollup from the live catalog ──────────────────────

def bootstrap_from_catalog(conn, mode='product'):
    """Seed aa_product_group AND aa_sku_group_map from skus in one pass, so every
    SKU rolls up to exactly one group (no name-match overlap, no category/SKU
    row mixing).

    mode='product'  → one group per product base name (part before ' - ', merges
      size/colour variants). Recommended — matches how ads are named.
    mode='category' → one group per skus.category (fewer, coarser).
    Returns {'groups': n, 'skus_mapped': m}.
    """
    if mode == 'category':
        rows = conn.execute(text("""
            SELECT sku, category AS grp, category FROM skus
            WHERE is_active = TRUE AND category IS NOT NULL AND category <> ''
        """)).fetchall()
    else:
        rows = conn.execute(text("""
            SELECT sku, trim(split_part(product_name, ' - ', 1)) AS grp, category
            FROM skus
            WHERE is_active = TRUE AND product_name IS NOT NULL AND product_name <> ''
              AND trim(split_part(product_name, ' - ', 1)) <> ''
        """)).fetchall()

    group_ids = {}
    skus_mapped = 0
    for r in rows:
        m = r._mapping
        grp = (m['grp'] or '').strip()[:200]
        sku = m['sku']
        if not grp or not sku:
            continue
        if grp not in group_ids:
            group_ids[grp] = create_group(conn, grp, category=m['category'])
        upsert_sku_map(conn, sku, group_ids[grp])
        skus_mapped += 1
    conn.commit()
    return {'groups': len(group_ids), 'skus_mapped': skus_mapped}


# ── Unmapped ad sets (biggest first) ─────────────────────────────────────────

def unmapped_adsets(conn, since, until, limit=60):
    """Ad sets with spend in the window that are NOT yet mapped to any group,
    largest spend first — the work list for closing the unmapped-spend gap."""
    mapped = {str(m['meta_adset_id']) for m in list_maps(conn) if m.get('meta_adset_id')}
    rows = conn.execute(text("""
        SELECT adset_id,
               MAX(adset_name)    AS adset_name,
               MAX(campaign_name) AS campaign_name,
               SUM(COALESCE(spend_bdt,0)) AS spend
        FROM ad_spend
        WHERE account_id = :acct AND adset_id IS NOT NULL
          AND date BETWEEN :since AND :until
        GROUP BY adset_id
        ORDER BY spend DESC
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchall()
    out = []
    for r in rows:
        m = r._mapping
        if str(m['adset_id']) in mapped:
            continue
        out.append({'adset_id': m['adset_id'], 'adset_name': m['adset_name'],
                    'campaign_name': m['campaign_name'], 'spend': float(m['spend'] or 0)})
        if len(out) >= limit:
            break
    return out


# ── Ad-level unmapped + suggester (from aa_meta_ad_daily) ────────────────────

def unmapped_ads(conn, since, until, limit=80):
    """Ads with spend in the window not yet mapped to a group, largest first."""
    mapped = {str(m['meta_ad_id']) for m in list_maps(conn) if m.get('meta_ad_id')}
    rows = conn.execute(text("""
        SELECT ad_id, MAX(ad_name) AS ad_name, MAX(adset_name) AS adset_name,
               MAX(campaign_name) AS campaign_name, SUM(COALESCE(spend_bdt,0)) AS spend
        FROM aa_meta_ad_daily
        WHERE account_id = :acct AND date BETWEEN :since AND :until
        GROUP BY ad_id ORDER BY spend DESC
    """), {'acct': RAGNAR_ACCOUNT_ID, 'since': since, 'until': until}).fetchall()
    out = []
    for r in rows:
        m = r._mapping
        if str(m['ad_id']) in mapped:
            continue
        out.append({'ad_id': m['ad_id'], 'ad_name': m['ad_name'],
                    'adset_name': m['adset_name'], 'campaign_name': m['campaign_name'],
                    'spend': float(m['spend'] or 0)})
        if len(out) >= limit:
            break
    return out


def suggest_ads(conn, since, until):
    """Suggest (ad -> group) by matching a group name inside the ad / ad-set /
    campaign name. Proposals only — admin confirms (nothing is written)."""
    groups = list_groups(conn)
    if not groups:
        return []
    proposals = []
    for a in unmapped_ads(conn, since, until):
        hay = f"{a['ad_name'] or ''} {a['adset_name'] or ''} {a['campaign_name'] or ''}".lower()
        best = None
        for g in groups:
            gname = (g['name'] or '').lower()
            if gname and gname in hay and (best is None or len(gname) > len(best[1])):
                best = (g, gname)
        if best:
            proposals.append({'ad_id': a['ad_id'], 'ad_name': a['ad_name'],
                              'spend': a['spend'], 'product_group_id': best[0]['id'],
                              'group_name': best[0]['name']})
    return proposals


# ── Name-based suggester (ad-set level, legacy) ──────────────────────────────

def suggest(conn):
    """Suggest (adset -> group) links for ad sets not yet mapped, by matching a
    group name inside the ad-set or campaign name. Returns proposals; the admin
    confirms them (nothing is written here)."""
    groups = list_groups(conn)
    if not groups:
        return []
    mapped = {str(m['meta_adset_id']) for m in list_maps(conn) if m.get('meta_adset_id')}

    adsets = conn.execute(text("""
        SELECT DISTINCT adset_id, adset_name, campaign_id, campaign_name
        FROM ad_spend
        WHERE account_id = :acct AND adset_id IS NOT NULL
          AND date > NOW() - INTERVAL '45 days'
    """), {'acct': RAGNAR_ACCOUNT_ID}).fetchall()

    proposals = []
    for r in adsets:
        m = r._mapping
        if str(m['adset_id']) in mapped:
            continue
        hay = f"{m['adset_name'] or ''} {m['campaign_name'] or ''}".lower()
        best = None
        for g in groups:
            gname = (g['name'] or '').lower()
            if gname and gname in hay:
                if best is None or len(gname) > len(best[1]):
                    best = (g, gname)
        if best:
            proposals.append({
                'adset_id': m['adset_id'], 'adset_name': m['adset_name'],
                'campaign_name': m['campaign_name'],
                'product_group_id': best[0]['id'], 'group_name': best[0]['name'],
            })
    return proposals
