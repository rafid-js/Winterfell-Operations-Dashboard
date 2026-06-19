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

# wp/v2/media is a WordPress-core endpoint, not WooCommerce — it doesn't accept
# WooCommerce's consumer key/secret. It needs a WP Application Password instead
# (wp-admin → Users → Profile → Application Passwords).
WP_USERNAME     = os.getenv('WP_USERNAME')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD')

_category_id_cache = {}


def _basic_auth_header() -> dict:
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _oauth1():
    return OAuth1(WOOCOMMERCE_KEY, WOOCOMMERCE_SECRET)


def upload_image(image_base64: str, filename: str = None, media_type: str = "image/jpeg") -> dict:
    """Upload an image to the WordPress media library. Returns {media_id, source_url}."""
    if not WP_USERNAME or not WP_APP_PASSWORD:
        raise RuntimeError(
            "WP_USERNAME / WP_APP_PASSWORD not configured — generate a WordPress "
            "Application Password (wp-admin → Users → Profile → Application Passwords) "
            "and set both env vars."
        )
    image_bytes = base64.b64decode(image_base64)
    ext = (media_type or "image/jpeg").split('/')[-1]
    filename = filename or f"wf-product-{int(time.time())}.{ext}"

    headers = _basic_auth_header()
    headers["Content-Type"] = media_type or "image/jpeg"
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    r = requests.post(
        f"{WOOCOMMERCE_URL}/wp-json/wp/v2/media",
        headers=headers, data=image_bytes, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {"media_id": data["id"], "source_url": data.get("source_url")}


def _resolve_category_id(category_slug: str):
    """Look up a WooCommerce category id by slug via the REST API, creating it if missing."""
    if not category_slug:
        return None
    if category_slug in _category_id_cache:
        return _category_id_cache[category_slug]

    r = requests.get(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/categories",
        auth=_oauth1(), params={"slug": category_slug}, timeout=15,
    )
    r.raise_for_status()
    matches = r.json()
    if matches:
        category_id = matches[0]["id"]
    else:
        name = category_slug.replace('-', ' ').title()
        r = requests.post(
            f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/categories",
            auth=_oauth1(), json={"name": name, "slug": category_slug}, timeout=15,
        )
        r.raise_for_status()
        category_id = r.json()["id"]

    _category_id_cache[category_slug] = category_id
    return category_id


def create_product(content: dict, media_id: int, category_slug: str, price: str = "0") -> dict:
    """Create a draft WooCommerce product. Returns {product_id, slug, preview_url}."""
    category_id = _resolve_category_id(category_slug)
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
