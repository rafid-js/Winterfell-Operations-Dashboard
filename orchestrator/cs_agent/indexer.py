"""Build the product_embeddings image-match index from skus.

skus is one row per size variant, so we group by product_name, aggregate live
stock per size into stock_json, and embed a rich description of the product.
Run once after migration, then nightly to keep stock current.

Run from orchestrator/:
  python -m cs_agent.indexer
"""
import os
import sys

from sqlalchemy import text

_BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain')
sys.path.insert(0, _BRAIN)
from db import get_connection  # noqa: E402

from . import config, embeddings  # noqa: E402


def _normalize_size(raw):
    s = (raw or '').strip().upper()
    return s if s in config.SIZES else (s or 'OS')


def _build_products(conn):
    """Group active skus into base products keyed by product_name."""
    rows = conn.execute(text("""
        SELECT sku, product_name, category, color, size, current_stock,
               selling_price, wc_product_id, image_url
        FROM skus
        WHERE is_active = TRUE AND product_name IS NOT NULL AND product_name <> ''
    """)).fetchall()

    products = {}
    for r in rows:
        m = r._mapping
        name = m['product_name']
        p = products.setdefault(name, {
            'representative_sku': m['sku'], 'category': m['category'],
            'image_url': m['image_url'], 'woo_product_id': m['wc_product_id'],
            'colors': set(), 'stock': {}, 'prices': [],
        })
        if m['color']:
            p['colors'].add(m['color'])
        if m['image_url'] and not p['image_url']:
            p['image_url'] = m['image_url']
        if m['wc_product_id'] and not p['woo_product_id']:
            p['woo_product_id'] = m['wc_product_id']
        size = _normalize_size(m['size'])
        p['stock'][size] = p['stock'].get(size, 0) + int(m['current_stock'] or 0)
        if m['selling_price'] and float(m['selling_price']) > 0:
            p['prices'].append(float(m['selling_price']))
    return products


def _description(name, p):
    colors = ', '.join(sorted(p['colors'])) or 'assorted'
    sizes = ', '.join(s for s in config.SIZES if s in p['stock']) or 'M-3XL'
    price = int(min(p['prices'])) if p['prices'] else 0
    return (f"{name}. Category: {p['category'] or 'apparel'}. Colours: {colors}. "
            f"Available sizes: {sizes}. Price: {price} taka. "
            f"Winterfell Gen Z streetwear, Bangladesh.")


def run():
    import json
    indexed = skipped = 0
    with get_connection() as conn:
        products = _build_products(conn)
        print(f"[cs_index] {len(products)} base products to index")

        for name, p in products.items():
            desc = _description(name, p)
            vec = embeddings.embed(desc)
            if vec is None:
                skipped += 1
                continue
            price = int(min(p['prices'])) if p['prices'] else None
            conn.execute(text("""
                INSERT INTO product_embeddings (
                  product_name, representative_sku, woo_product_id, category,
                  image_url, description_text, description_embedding, stock_json,
                  price, is_active, updated_at)
                VALUES (
                  :pn, :rs, :wid, :cat, :img, :desc, CAST(:emb AS vector),
                  CAST(:stock AS jsonb), :price, TRUE, NOW())
                ON CONFLICT (product_name) DO UPDATE SET
                  representative_sku = EXCLUDED.representative_sku,
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
                'pn': name[:300], 'rs': p['representative_sku'],
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
