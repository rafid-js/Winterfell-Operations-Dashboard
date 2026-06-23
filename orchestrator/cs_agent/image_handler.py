"""Image path — describe the photo with Claude Vision, match it to a product via
pgvector cosine similarity, and reply with live stock.

High-confidence match → reply with stock immediately.
Low-confidence match → ask the customer to confirm (stored as pending_confirmation).
"""
import base64

import requests
from sqlalchemy import text

from . import config, memory, embeddings
from .claude_client import client
from .reply import send_reply
from .handoff import trigger_handoff

_VISION_PROMPT = (
    "You are a product identifier for Winterfell, a Bangladesh Gen Z fashion brand.\n"
    "Describe this clothing item in detail for product matching. Include: type "
    "(cargo pants, drop-shoulder tee, denim jacket, etc.), primary and secondary "
    "colour, key design features (pockets, prints, distressing), fabric appearance, "
    "fit style (oversized/slim/relaxed), and any visible text/logos/graphics.\n"
    "Return a single descriptive paragraph. Be specific and factual."
)


def _download_image(url, channel):
    headers = {}
    # WATI media URLs sit behind the same bearer token.
    if channel == 'whatsapp' and config.WATI_API_KEY:
        headers['Authorization'] = f'Bearer {config.WATI_API_KEY}'
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    media_type = r.headers.get('content-type', 'image/jpeg').split(';')[0]
    if media_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        media_type = 'image/jpeg'
    return base64.b64encode(r.content).decode('utf-8'), media_type


def _describe(image_b64, media_type):
    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64',
                                             'media_type': media_type, 'data': image_b64}},
                {'type': 'text', 'text': _VISION_PROMPT},
            ],
        }],
    )
    return ''.join(b.text for b in resp.content if b.type == 'text').strip()


def _match(conn, description):
    vec = embeddings.embed(description)
    if vec is None:
        return None
    rows = conn.execute(text("""
        SELECT representative_sku, product_name, stock_json, price,
               1 - (description_embedding <=> CAST(:emb AS vector)) AS similarity
        FROM product_embeddings
        WHERE is_active = TRUE AND description_embedding IS NOT NULL
        ORDER BY description_embedding <=> CAST(:emb AS vector)
        LIMIT 3
    """), {'emb': embeddings.to_pgvector(vec)}).fetchall()
    return rows[0]._mapping if rows else None


def handle_image(conn, session, image_url):
    channel, customer_id = session['channel'], session['customer_id']

    try:
        image_b64, media_type = _download_image(image_url, channel)
        description = _describe(image_b64, media_type)
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Vision/download failed: {e}', flush=True)
        reply = ("ভাই আপনার ছবিটা এই মুহূর্তে process করতে পারছি না 🙏 "
                 "আমাদের team একটু পরে reply করবে।")
        send_reply(channel, customer_id, reply)
        trigger_handoff(conn, session, reason='Image processing failed')
        memory.append_turn(session, '[image]', reply)
        return

    top = _match(conn, description)
    if not top:
        reply = ("ভাই, আপনার ছবিটা দেখলাম — এই মুহূর্তে match করতে পারছি না। "
                 "আমাদের team কিছুক্ষণের মধ্যে reply করবে 🙏")
        send_reply(channel, customer_id, reply)
        trigger_handoff(conn, session, reason='No product match for image')
        memory.append_turn(session, '[image]', reply)
        return

    similarity = float(top['similarity'] or 0)
    product_name = top['product_name']
    representative_sku = top['representative_sku']
    price = int(top['price'] or 0)
    stock = memory._coerce(top['stock_json'], {})

    if similarity >= config.SIMILARITY_THRESHOLD:
        line = config.stock_line(stock)
        if line:
            reply = (f"হ্যাঁ ভাই! এটা আমাদের {product_name} 🔥\n\n"
                     f"✅ স্টকে আছে — ৳{price}\n📦 Available: {line}\n\n"
                     f"কোন সাইজটা নিতে চাচ্ছেন?")
        else:
            reply = (f"এটা আমাদের {product_name} — কিন্তু এই মুহূর্তে stock নেই 😔 "
                     f"Restock হলে page এ update দেব। Follow করে রাখুন 🙏")
        memory.append_turn(session, '[image]', reply)
        send_reply(channel, customer_id, reply)
    else:
        reply = (f"এটা কি আমাদের {product_name} (৳{price})?\n"
                 f"'হ্যাঁ' বা 'না' reply করুন — তাহলে stock check করে দিচ্ছি 🙏")
        session['pending_confirmation'] = {
            'representative_sku': representative_sku, 'product_name': product_name}
        memory.append_turn(session, '[image]', reply)
        send_reply(channel, customer_id, reply)
