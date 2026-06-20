"""WooCommerce REST API calls — media upload, product create/publish/delete."""
import os
import base64
import time

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

WOOCOMMERCE_URL    = (os.getenv('WC_URL') or os.getenv('WOOCOMMERCE_URL') or '').rstrip('/')
WOOCOMMERCE_KEY    = os.getenv('WC_CONSUMER_KEY') or os.getenv('WOOCOMMERCE_KEY')
WOOCOMMERCE_SECRET = os.getenv('WC_CONSUMER_SECRET') or os.getenv('WOOCOMMERCE_SECRET')

# wp/v2/media is a WordPress-core endpoint, not WooCommerce — it doesn't accept
# WooCommerce's consumer key/secret. It needs a WP Application Password instead
# (wp-admin → Users → Profile → Application Passwords).
WP_USERNAME     = os.getenv('WP_USERNAME')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD')

_category_id_cache = {}
_all_categories_cache = None


def _normalize(s: str) -> str:
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def _all_categories() -> list:
    global _all_categories_cache
    if _all_categories_cache is None:
        r = requests.get(
            f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/categories",
            auth=_oauth1(), params={"per_page": 100}, timeout=15,
        )
        r.raise_for_status()
        _all_categories_cache = r.json()
    return _all_categories_cache


def _basic_auth_header() -> dict:
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _oauth1():
    if not WOOCOMMERCE_KEY or not WOOCOMMERCE_SECRET:
        raise RuntimeError(
            "WC_CONSUMER_KEY / WC_CONSUMER_SECRET not configured — set both env vars "
            "(WooCommerce → Settings → Advanced → REST API)."
        )
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


def get_category_reference_description(category_slug: str):
    """Return the description of the most recently published product in this category,
    for reuse as a size-chart reference. None if the category is empty/unmatched."""
    category_id = _resolve_category_id(category_slug)
    if not category_id:
        return None
    r = requests.get(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products",
        auth=_oauth1(),
        params={"category": category_id, "status": "publish", "per_page": 1, "orderby": "date", "order": "desc"},
        timeout=15,
    )
    r.raise_for_status()
    items = r.json()
    return items[0].get("description") if items else None


def _resolve_category_id(category_slug: str):
    """Match a guessed category slug against existing WooCommerce categories, only
    creating a new one if nothing close already exists."""
    if not category_slug:
        return None
    if category_slug in _category_id_cache:
        return _category_id_cache[category_slug]

    target = _normalize(category_slug)
    categories = _all_categories()

    for cat in categories:
        if _normalize(cat["slug"]) == target or _normalize(cat["name"]) == target:
            _category_id_cache[category_slug] = cat["id"]
            return cat["id"]

    for cat in categories:
        cat_norm = _normalize(cat["slug"])
        if target in cat_norm or cat_norm in target:
            _category_id_cache[category_slug] = cat["id"]
            return cat["id"]

    name = category_slug.replace('-', ' ').title()
    r = requests.post(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/categories",
        auth=_oauth1(), json={"name": name, "slug": category_slug}, timeout=15,
    )
    r.raise_for_status()
    category_id = r.json()["id"]
    categories.append(r.json())
    _category_id_cache[category_slug] = category_id
    return category_id


DEFAULT_WEIGHT_KG = "0.25"
SIZES = ["M", "L", "XL", "XXL", "3XL"]


def create_product(content: dict, media_id: int, category_slug: str, price: str = "0",
                    weight: str = None, sale_price: str = None) -> dict:
    """Create and immediately publish a variable WooCommerce product with a selectable
    Size attribute, then create one price-bearing variation per size. Returns
    {product_id, slug, permalink}."""
    category_id = _resolve_category_id(category_slug)
    body = {
        "name":              content["product_name"],
        "type":              "variable",
        "status":            "publish",
        "description":       content["long_description"],
        "short_description": content["short_description"],
        "weight":            str(weight or DEFAULT_WEIGHT_KG),
        "categories":        [{"id": int(category_id)}] if category_id else [],
        "tags":              [{"name": tag} for tag in content.get("woo_tags", [])],
        "images":            [{"id": media_id}],
        "attributes": [{
            "name": "Size",
            "visible": True,
            "variation": True,
            "options": SIZES,
        }],
        "default_attributes": [{
            "name": "Size",
            "option": "L",
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
    product_id = data["id"]

    create_size_variations(product_id, price, sale_price)

    return {
        "product_id": product_id,
        "slug":       data.get("slug"),
        "permalink":  data.get("permalink"),
    }


def create_size_variations(product_id: int, regular_price: str, sale_price: str = None):
    """Create one variation per Size option, all sharing the same regular/sale price."""
    for size in SIZES:
        body = {
            "regular_price": str(regular_price or "0"),
            "attributes": [{"name": "Size", "option": size}],
        }
        if sale_price:
            body["sale_price"] = str(sale_price)
        r = requests.post(
            f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}/variations",
            auth=_oauth1(), json=body, timeout=30,
        )
        r.raise_for_status()


def update_variation_prices(product_id: int, regular_price: str = None, sale_price: str = None):
    """Update regular/sale price on every existing variation of a product. Falls back to
    setting the price directly on the product itself if it has no variations (e.g. a
    simple product created before this product became variable)."""
    if regular_price is None and sale_price is None:
        return
    r = requests.get(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}/variations",
        auth=_oauth1(), params={"per_page": 100}, timeout=15,
    )
    r.raise_for_status()
    variations = r.json()
    body = {}
    if regular_price is not None:
        body["regular_price"] = str(regular_price)
    if sale_price is not None:
        body["sale_price"] = str(sale_price)

    if not variations:
        rr = requests.put(
            f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
            auth=_oauth1(), json=body, timeout=30,
        )
        rr.raise_for_status()
        return

    for variation in variations:
        rr = requests.put(
            f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}/variations/{variation['id']}",
            auth=_oauth1(), json=body, timeout=30,
        )
        rr.raise_for_status()


def get_product(product_id: int) -> dict:
    """Fetch a product's current data, e.g. to read its live description before editing it."""
    r = requests.get(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), timeout=15,
    )
    r.raise_for_status()
    return r.json()


def update_product_description(product_id: int, description: str) -> dict:
    """Overwrite a product's description (e.g. after a correction)."""
    r = requests.put(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), json={"description": description}, timeout=30,
    )
    r.raise_for_status()
    return {"permalink": r.json().get("permalink")}


def update_product_category(product_id: int, category_slug: str) -> dict:
    """Re-categorize an existing product. Returns {category}."""
    category_id = _resolve_category_id(category_slug)
    r = requests.put(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), json={"categories": [{"id": int(category_id)}] if category_id else []},
        timeout=30,
    )
    r.raise_for_status()
    return {"category": category_slug}


def publish_product(product_id: int, price: str = None, sale_price: str = None) -> dict:
    """Ensure a product is published, optionally updating its variation prices. Returns {permalink}."""
    r = requests.put(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), json={"status": "publish"}, timeout=30,
    )
    r.raise_for_status()
    update_variation_prices(product_id, price, sale_price)
    return {"permalink": r.json().get("permalink")}


def delete_product(product_id: int) -> dict:
    """Permanently delete a WooCommerce product."""
    r = requests.delete(
        f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products/{product_id}",
        auth=_oauth1(), params={"force": "true"}, timeout=30,
    )
    r.raise_for_status()
    return {"deleted": True}
