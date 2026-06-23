"""Build the product_embeddings image-match index from skus.

The skus table is messy: one row per size variant PLUS a parent row, with size
(and often colour) baked into product_name as a suffix and the structured
size/color columns frequently NULL. Variants share a SKU parent — e.g. 21551,
21551-21552, 21551-21553 all belong to base product 21551 — so we group on
split_part(sku,'-',1), not on the name string. Each base product is one
colourway (colours stay separate; they look different in a photo); its size
variants collapse into one stock_json.

Run once after migration, then nightly to keep stock current.

Run from orchestrator/:
  python -m cs_agent.indexer
"""
import json
import os
import sys
from collections import Counter

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from db import get_connection  # noqa: E402

from . import config, embeddings  # noqa: E402


def _strip_size_suffix(name, size):
    """Drop a trailing ' - <size>' so variants reduce to the base name.

    Only strips when the suffix matches this row's own size value, so colour
    suffixes ('- Off white') are never removed.
    """
    name = (name or '').strip()
    if size:
        suffix = f" - {size}".strip()
        if name.lower().endswith(suffix.lower()):
            return name[: len(name) - len(suffix)].strip(' -')
    return name


def _clean_size(raw):
    s = (raw or '').strip()
    return s or None


def _pick_name(names):
    """Most common cleaned name in the group; tie-break on the shortest."""
    names = [n for n in names if n]
    if not names:
        return None
    counts = Counter(names)
    return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


def _build_products(conn):
    """Group active skus into base products keyed by SKU parent."""
    rows = conn.execute(text("""
        SELECT sku, product_name, category, color, size, current_stock,
               selling_price, wc_product_id, image_url
        FROM skus
        WHERE is_active = TRUE
          AND sku IS NOT NULL AND sku <> ''
          AND product_name IS NOT NULL AND product_name <> ''
    """)).fetchall()

    groups = {}
    for r in rows:
        m = r._mapping
        sku = m['sku'].strip()
        parent = sku.split('-')[0]
        g = groups.setdefault(parent, {
            'representative_sku': parent, 'parent_name': None, 'names': [],
            'category': None, 'image_url': None, 'color': None,
            'woo_product_id': None, 'prices': [],
            'stock': {}, 'parent_stock': 0, 'has_sized': False,
        })

        size = _clean_size(m['size'])
        stock = int(m['current_stock'] or 0)
        clean = _strip_size_suffix(m['product_name'], size)
        g['names'].append(clean)

        if not g['category'] and m['category']:
            g['category'] = m['category']
        if not g['image_url'] and m['image_url']:
            g['image_url'] = m['image_url']
        if not g['color'] and m['color']:
            g['color'] = m['color']
        if not g['woo_product_id'] and m['wc_product_id']:
            g['woo_product_id'] = m['wc_product_id']
        price = float(m['selling_price'] or 0)
        if price > 0:
            g['prices'].append(price)

        if sku == parent:                      # the parent row
            g['parent_name'] = clean
            g['parent_stock'] = stock
        if size:                               # a real size variant
            g['stock'][size] = g['stock'].get(size, 0) + stock
            g['has_sized'] = True

    products = {}
    for parent, g in groups.items():
        name = g['parent_name'] or _pick_name(g['names'])
        if not name:
            continue
        stock = dict(g['stock'])
        # One-size product (no sized variants) — fall back to the parent stock.
        if not g['has_sized'] and g['parent_stock']:
            stock = {'OS': g['parent_stock']}
        products[parent] = {
            'representative_sku': parent, 'product_name': name,
            'category': g['category'], 'image_url': g['image_url'],
            'color': g['color'], 'woo_product_id': g['woo_product_id'],
            'prices': g['prices'], 'stock': stock,
        }
    return products


def _description(p):
    sizes = ', '.join(p['stock'].keys()) or 'one size'
    price = int(min(p['prices'])) if p['prices'] else 0
    parts = [f"{p['product_name']}."]
    if p['category']:
        parts.append(f"Category: {p['category']}.")
    if p['color']:
        parts.append(f"Colour: {p['color']}.")
    parts.append(f"Available sizes: {sizes}.")
    parts.append(f"Price: {price} taka.")
    parts.append("Winterfell Gen Z streetwear, Bangladesh.")
    return ' '.join(parts)


def run():
    indexed = skipped = 0
    with get_connection() as conn:
        products = _build_products(conn)
        print(f"[cs_index] {len(products)} base products to index")

        for parent, p in products.items():
            desc = _description(p)
            vec = embeddings.embed(desc)
            if vec is None:
                skipped += 1
                continue
            price = int(min(p['prices'])) if p['prices'] else None
            conn.execute(text("""
                INSERT INTO product_embeddings (
                  representative_sku, product_name, woo_product_id, category,
                  image_url, description_text, description_embedding, stock_json,
                  price, is_active, updated_at)
                VALUES (
                  :rs, :pn, :wid, :cat, :img, :desc, CAST(:emb AS vector),
                  CAST(:stock AS jsonb), :price, TRUE, NOW())
                ON CONFLICT (representative_sku) DO UPDATE SET
                  product_name = EXCLUDED.product_name,
                  woo_product_id = EXCLUDED.woo_product_id,
                  category = EXCLUDED.category,
                  image_url = EXCLUDED.image_url,
                  description_text = EXCLUDED.description_text,
                  description_embedding = EXCLUDED.description_embedding,
                  stock_json = EXCLUDED.stock_json,
                  price = EXCLUDED.price,
                  is_active = TRUE,
                  updated_at = NOW()
            """), {
                'rs': parent[:200], 'pn': p['product_name'][:300],
                'wid': p['woo_product_id'], 'cat': p['category'],
                'img': p['image_url'], 'desc': desc,
                'emb': embeddings.to_pgvector(vec),
                'stock': json.dumps(p['stock']), 'price': price,
            })
            indexed += 1

        conn.commit()

    msg = f"cs_index: {indexed} products indexed, {skipped} skipped"
    print(f"[cs_index] {msg}")
    return {'indexed': indexed, 'skipped': skipped}


if __name__ == '__main__':
    run()
