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


# ── Name-based suggester ─────────────────────────────────────────────────────

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
