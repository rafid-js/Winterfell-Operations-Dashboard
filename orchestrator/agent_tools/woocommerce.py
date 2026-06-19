"""WooCommerce REST API calls — media upload, product create/publish/delete."""
import os
import base64
import time

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

WOOCOMMERCE_URL    = (os.getenv('WOOCOMMERCE_URL') or '').rstrip('/')
WOOCOMMERCE_KEY    = os.getenv('WOOCOMMERCE_KEY')
WOOCOMMERCE_SECRET = os.getenv('WOOCOMMERCE_SECRET')

CATEGORY_IDS = {
    'cargo-pants':       os.getenv('CAT_CARGO_PANTS'),
    'drop-shoulder-tee': os.getenv('CAT_DROP_SHOULDER_TEE'),
    'denim':             os.getenv('CAT_DENIM'),
    'knit-polo':         os.getenv('CAT_KNIT_POLO'),
    'jogger':            os.getenv('CAT_JOGGER'),
    'jacket':            os.getenv('CAT_JACKET'),
    'other':             os.getenv('CAT_OTHER'),
}


def _basic_auth_header() -> dict:
    token = base64.b64encode(f"{WOOCOMMERCE_KEY}:{WOOCOMMERCE_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _oauth1():
    return OAuth1(WOOCOMMERCE_KEY, WOOCOMMERCE_SECRET)


def upload_image(image_base64: str, filename: str = None) -> dict:
    """Upload an image to the WordPress media library. Returns {media_id, source_url}."""
    image_bytes = base64.b64decode(image_base64)
    filename = filename or f"wf-product-{int(time.time())}.jpg"

    headers = _basic_auth_header()
    headers["Content-Type"] = "image/jpeg"
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    r = requests.post(
        f"{WOOCOMMERCE_URL}/wp-json/wp/v2/media",
        headers=headers, data=image_bytes, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {"media_id": data["id"], "source_url": data.get("source_url")}


def create_product(content: dict, media_id: int, category_slug: str, price: str = "0") -> dict:
    """Create a draft WooCommerce product. Returns {product_id, slug, preview_url}."""
    category_id = CATEGORY_IDS.get(category_slug)
    body = {
        "name":              content["product_name"],
        "status":            "draft",
        "description":       content["long_description"],
        "short_description": content["short_description"],
        "regular_price":     str(price or "0"),
        "categories":        [{"id": int(category_id)}] if category_id else [],
        "tags":              [{"name": tag} for tag in content.get("woo_tags", [])],
        "images":            [{"id": media_id}],
        "attributes": [{
            "name": "Size",
            "visible": True,
            "options": ["S", "M", "L", "XL"],
        }],
        "meta_data": [
            {"key": "_yoast_wpseo_title", "value": content.get("seo_title", "")},
            {"key": "_yoast_wpseo_metadesc", "value": content.get("seo_meta_description", "")},
        ],
    }
    r = requests.post(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products",
        auth=_oauth1(), json=body, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "product_id":  data["id"],
        "slug":        data.get("slug"),
        "preview_url": f"{WOOCOMMERCE_URL}/?p={data['id']}&preview=true",
    }


def publish_product(product_id: int, price: str = None) -> dict:
    """Publish a draft product, optionally setting price first. Returns {permalink}."""
    body = {"status": "publish"}
    if price is not None:
        body["regular_price"] = str(price)

    r = requests.put(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), json=body, timeout=30,
    )
    r.raise_for_status()
    return {"permalink": r.json().get("permalink")}


def delete_product(product_id: int) -> dict:
    """Permanently delete a WooCommerce product."""
    r = requests.delete(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), params={"force": "true"}, timeout=30,
    )
    r.raise_for_status()
    return {"deleted": True}
