#!/usr/bin/env python3
"""
Winterfell Operations — Nuport OMS → Zoho Books inventory sync  v3
- WooCommerce SKU whitelist (only products live on winterfellbd.com)
- Winterfell Warehouse stock only
- Sale price used as rate (fallback to base price)
- Purchase price synced
- Stock quantity synced via Zoho inventory adjustments
- Local change tracking: only syncs items that actually changed since last run
"""

import json
import os
import sys
import time
import base64
import logging
from datetime import datetime
import urllib.request
import urllib.parse
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPT_DIR, "config.json")
STATE_FILE   = os.path.join(SCRIPT_DIR, "last_sync_state.json")

WINTERFELL_WAREHOUSE_KEYWORD = "winterfell"  # case-insensitive match on location label


# ── Config ───────────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print("ERROR: config.json not found.")
        print("Copy config.example.json to config.json and fill in your credentials.")
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: config.json has invalid JSON: {e}")
        sys.exit(1)


# ── Change tracking ──────────────────────────────────────────────────────────

def load_last_state():
    """Load the saved state from the previous sync run."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(products):
    """Persist catalogue fields only — quantity is always compared live against Zoho."""
    state = {
        sku: {
            "name":          p["name"],
            "rate":          p["rate"],
            "purchase_rate": p["purchase_rate"],
        }
        for sku, p in products.items()
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def filter_changed(products, last_state):
    """
    Return only products whose catalogue fields (name, rate, purchase_rate)
    changed since the last run. Quantity is intentionally excluded — stock is
    always compared live against Zoho so timeouts never cause it to be skipped.
    """
    changed = {}
    unchanged = 0
    for sku, product in products.items():
        prev = last_state.get(sku)
        if prev is None or (
            product["name"]          != prev.get("name") or
            product["rate"]          != prev.get("rate") or
            product["purchase_rate"] != prev.get("purchase_rate")
        ):
            changed[sku] = product
        else:
            unchanged += 1

    logging.info(
        f"Catalogue change detection: {len(changed)} changed/new, "
        f"{unchanged} unchanged (price/name) — skipping those Zoho updates"
    )
    return changed


# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(log_dir):
    log_dir_abs = os.path.join(SCRIPT_DIR, log_dir)
    os.makedirs(log_dir_abs, exist_ok=True)
    log_file = os.path.join(log_dir_abs, f"sync_{datetime.now().strftime('%Y-%m-%d')}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _do_request(req, label):
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise Exception(f"{label} — HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise Exception(f"{label} — network error: {e.reason}")


def http_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    return _do_request(req, f"GET {url}")


def http_post(url, payload, headers):
    body = json.dumps(payload).encode("utf-8")
    h = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    return _do_request(req, f"POST {url}")


def http_put(url, payload, headers):
    body = json.dumps(payload).encode("utf-8")
    h = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=h, method="PUT")
    return _do_request(req, f"PUT {url}")


def zoho_request(method, url, payload, headers):
    fn = {"GET": http_get, "POST": http_post, "PUT": http_put}[method]
    args = (url, headers) if method == "GET" else (url, payload, headers)
    try:
        return fn(*args)
    except Exception as e:
        if "429" in str(e):
            logging.warning("Zoho rate limit hit — waiting 10 seconds and retrying...")
            time.sleep(10)
            return fn(*args)
        raise


# ── Step 1: WooCommerce SKU whitelist ────────────────────────────────────────

def fetch_woocommerce_skus(config):
    """
    Fetches all published product SKUs from WooCommerce.
    Covers simple products + all variation SKUs for variable products.
    Returns a set of SKU strings.
    """
    site = config["woo_site_url"].rstrip("/")
    base = f"{site}/wp-json/wc/v3"
    key = config["woo_consumer_key"]
    secret = config["woo_consumer_secret"]
    # Pass credentials as query params to avoid Cloudflare blocking Authorization headers
    auth_params = f"consumer_key={key}&consumer_secret={secret}"
    headers = {"User-Agent": "WinterFell-Sync/2.0"}

    skus = set()
    variable_ids = []

    logging.info("Fetching active SKUs from WooCommerce...")
    page = 1
    while True:
        url = f"{base}/products?status=publish&per_page=100&page={page}&{auth_params}"
        try:
            products = http_get(url, headers)
        except Exception as e:
            raise Exception(f"WooCommerce products fetch failed (page {page}): {e}")

        if not products:
            break

        for p in products:
            sku = (p.get("sku") or "").strip()
            if sku:
                skus.add(sku)
            if p.get("type") == "variable":
                variable_ids.append(p["id"])

        if len(products) < 100:
            break
        page += 1

    # Fetch variation SKUs for variable products
    for pid in variable_ids:
        var_page = 1
        while True:
            url = f"{base}/products/{pid}/variations?per_page=100&page={var_page}&{auth_params}"
            try:
                variations = http_get(url, headers)
            except Exception as e:
                logging.warning(f"Could not fetch variations for WooCommerce product {pid}: {e}")
                break

            if not variations:
                break

            for v in variations:
                sku = (v.get("sku") or "").strip()
                if sku:
                    skus.add(sku)

            if len(variations) < 100:
                break
            var_page += 1

    logging.info(f"WooCommerce: {len(skus)} active SKUs found on site")
    return skus


# ── Step 2: Fetch Nuport inventory ───────────────────────────────────────────

def fetch_nuport_inventory(config):
    """
    Calls GET /integration/inventory?page=-1 — returns ALL records in one shot.
    Tries multiple auth header formats until one works.
    """
    base = config["nuport_base_url"].rstrip("/")
    url = f"{base}/integration/inventory?page=-1"
    key = config["nuport_api_key"]

    auth_formats = [
        {"Authorization": key},
        {"Authorization": f"Bearer {key}"},
        {"X-API-Key": key},
        {"Authorization": f"ApiKey {key}"},
        {"Authorization": f"Token {key}"},
    ]

    logging.info("Fetching all inventory from Nuport...")
    last_error = None
    for headers in auth_formats:
        header_name = list(headers.keys())[0]
        try:
            data = http_get(url, headers)
            logging.info(f"Nuport auth succeeded ({header_name})")
            return data.get("results", []), data
        except Exception as e:
            if "401" in str(e):
                last_error = e
                continue
            raise

    raise Exception(f"All Nuport auth formats failed. Last error: {last_error}")


# ── Step 3: Parse + filter Nuport products ───────────────────────────────────

def parse_nuport_products(records, woo_skus):
    """
    Builds {sku → product_info} from Nuport inventory records.

    Filters applied:
    1. Location must be Winterfell Warehouse (label contains 'winterfell')
    2. SKU must exist in WooCommerce (live on site)
    3. Skip deleted products
    4. Skip missing SKU
    5. Skip zero/null price

    Price logic: use salePrice if > 0, else fall back to price (base/MRP)
    """
    products = {}
    skip_warehouse = skip_site = skip_deleted = skip_no_sku = skip_price = 0

    for record in records:
        product = record.get("product") or {}
        location = record.get("location") or {}

        # Filter 1: Winterfell Warehouse only
        location_label = (location.get("label") or "").lower()
        if WINTERFELL_WAREHOUSE_KEYWORD not in location_label:
            skip_warehouse += 1
            continue

        # Filter 2: skip deleted
        if product.get("deleted", False):
            skip_deleted += 1
            continue

        # Filter 3: must have SKU
        sku = (product.get("sku") or "").strip()
        if not sku:
            logging.warning(f"SKIP (no SKU): '{product.get('name', 'unnamed')}'")
            skip_no_sku += 1
            continue

        # Filter 4: must be live on WooCommerce
        if sku not in woo_skus:
            skip_site += 1
            continue

        # Price: salePrice first, fallback to price
        try:
            sale_price = float(product.get("salePrice") or 0)
        except (ValueError, TypeError):
            sale_price = 0.0

        try:
            base_price = float(product.get("price") or 0)
        except (ValueError, TypeError):
            base_price = 0.0

        rate = sale_price if sale_price > 0 else base_price

        if rate <= 0:
            logging.warning(f"SKIP (price=0): SKU '{sku}' — '{product.get('name', '')}'")
            skip_price += 1
            continue

        try:
            purchase_rate = float(product.get("purchasePrice") or 0)
        except (ValueError, TypeError):
            purchase_rate = 0.0

        qty = int(record.get("quantity") or 0)

        # When same SKU appears in multiple locations (shouldn't happen after warehouse
        # filter, but keep the highest-stock entry just in case)
        if sku not in products or qty > products[sku]["quantity"]:
            products[sku] = {
                "name":          product.get("name", "").strip(),
                "sku":           sku,
                "rate":          rate,
                "purchase_rate": purchase_rate,
                "quantity":      qty,
            }

    logging.info(
        f"Ready to sync: {len(products)} products  "
        f"(skipped — {skip_warehouse} wrong warehouse, {skip_site} not on site, "
        f"{skip_deleted} deleted, {skip_no_sku} no SKU, {skip_price} zero price)"
    )
    return products


# ── Zoho OAuth ───────────────────────────────────────────────────────────────

def get_zoho_token(config):
    region = config.get("zoho_region", "com")
    url = f"https://accounts.zoho.{region}/oauth/v2/token"
    data = urllib.parse.urlencode({
        "refresh_token": config["zoho_refresh_token"],
        "client_id":     config["zoho_client_id"],
        "client_secret": config["zoho_client_secret"],
        "grant_type":    "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise Exception(f"Zoho token request failed — HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise Exception(f"Zoho token request failed — network error: {e.reason}")

    if "access_token" not in result:
        raise Exception(f"Zoho token response missing access_token: {result}")

    logging.info("Zoho access token obtained.")
    return result["access_token"]


# ── Zoho Books — Items ───────────────────────────────────────────────────────

def fetch_zoho_items(config, token):
    """
    Pages through all Zoho Books items.
    Returns {sku → {item_id, account_id, inventory_account_id, stock_on_hand}}
    """
    region = config.get("zoho_region", "com")
    org_id = config["zoho_org_id"]
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    lookup = {}
    page = 1

    logging.info("Fetching existing items from Zoho Books...")
    while True:
        url = (
            f"https://www.zohoapis.{region}/books/v3/items"
            f"?organization_id={org_id}&page={page}&per_page=200"
        )
        try:
            data = zoho_request("GET", url, None, headers)
        except Exception as e:
            raise Exception(f"Zoho items fetch failed (page {page}): {e}")

        for item in data.get("items", []):
            sku = (item.get("sku") or "").strip()
            if sku:
                lookup[sku] = {
                    "item_id":              item["item_id"],
                    "account_id":           item.get("account_id", ""),
                    "inventory_account_id": item.get("inventory_account_id", ""),
                    "stock_on_hand":        float(item.get("stock_on_hand") or 0),
                    "item_type":            item.get("item_type", ""),
                    "status":               item.get("status", "active"),
                }

        page_ctx = data.get("page_context", {})
        if not page_ctx.get("has_more_page", False):
            break
        page += 1

    logging.info(f"Found {len(lookup)} SKU-mapped items in Zoho Books.")
    return lookup


def create_zoho_item(config, token, product):
    region = config.get("zoho_region", "com")
    org_id = config["zoho_org_id"]
    url = f"https://www.zohoapis.{region}/books/v3/items?organization_id={org_id}"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    payload = {
        "name":         product["name"],
        "sku":          product["sku"],
        "rate":         product["rate"],
        "item_type":    "inventory",
        "product_type": "goods",
    }
    if product["purchase_rate"] > 0:
        payload["purchase_rate"] = product["purchase_rate"]

    result = zoho_request("POST", url, payload, headers)
    if result.get("code") != 0:
        raise Exception(f"Zoho rejected create: {result.get('message', result)}")
    return result.get("item", {}).get("item_id")


def update_zoho_item(config, token, item_id, product, existing):
    region = config.get("zoho_region", "com")
    org_id = config["zoho_org_id"]
    url = (
        f"https://www.zohoapis.{region}/books/v3/items/{item_id}"
        f"?organization_id={org_id}"
    )
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    payload = {
        "name": product["name"],
        "rate": product["rate"],
    }
    if product["purchase_rate"] > 0:
        payload["purchase_rate"] = product["purchase_rate"]
    if existing.get("account_id"):
        payload["account_id"] = existing["account_id"]
    if existing.get("inventory_account_id"):
        payload["inventory_account_id"] = existing["inventory_account_id"]

    result = zoho_request("PUT", url, payload, headers)
    if result.get("code") != 0:
        raise Exception(f"Zoho rejected update: {result.get('message', result)}")


# ── Zoho Books — Inventory Adjustment ───────────────────────────────────────

def sync_zoho_stock(config, token, adjustment_items):
    """
    Sends all stock adjustments in one request.
    quantity_adjusted is the DIFFERENCE (positive = stock up, negative = stock down).
    If this times out, just run the script again — catalogue updates will be
    skipped (change tracking) and only stock will be retried.
    """
    if not adjustment_items:
        logging.info("Stock quantities already match — no adjustment needed.")
        return 0

    region = config.get("zoho_region", "com")
    org_id = config["zoho_org_id"]
    url = (
        f"https://www.zohoapis.{region}/books/v3/inventoryadjustments"
        f"?organization_id={org_id}"
    )
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    payload = {
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "reason":     "Nuport OMS auto-sync",
        "line_items": [
            {"item_id": i["item_id"], "quantity_adjusted": i["quantity_adjusted"]}
            for i in adjustment_items
        ],
    }

    result = zoho_request("POST", url, payload, headers)
    if result.get("code") != 0:
        raise Exception(f"Zoho inventory adjustment failed: {result.get('message', result)}")

    logging.info(f"Stock adjustment created for {len(adjustment_items)} items.")
    return len(adjustment_items)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    log_file = setup_logging(config.get("log_dir", "logs/"))

    logging.info("=" * 60)
    logging.info("Winterfell  |  Nuport → Zoho Books Sync  v2")
    logging.info(f"Started at  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Log file    {log_file}")
    logging.info("=" * 60)

    created = updated = errors = stock_adjusted = 0

    # ── Step 1: WooCommerce SKU whitelist ────────────────────────────────────
    try:
        woo_skus = fetch_woocommerce_skus(config)
    except Exception as e:
        logging.error(f"FATAL — cannot fetch WooCommerce SKUs: {e}")
        logging.error("Check woo_site_url, woo_consumer_key, woo_consumer_secret in config.json")
        sys.exit(1)

    # ── Step 2: Nuport inventory ─────────────────────────────────────────────
    try:
        records, raw_data = fetch_nuport_inventory(config)
        logging.info(
            f"Nuport returned {len(records)} inventory records "
            f"(API total: {raw_data.get('count', '?')})"
        )
    except Exception as e:
        logging.error(f"FATAL — cannot reach Nuport: {e}")
        logging.error("Check nuport_api_key and nuport_base_url in config.json")
        sys.exit(1)

    # ── Step 3: Filter + parse ───────────────────────────────────────────────
    nuport_products = parse_nuport_products(records, woo_skus)

    if not nuport_products:
        logging.warning(
            "No products passed all filters. "
            "Check warehouse name contains 'winterfell' and products have WooCommerce SKUs."
        )
        print("\nSync complete: 0 created, 0 updated, 0 errors")
        return

    # ── Step 4: Change detection — skip items identical to last run ──────────
    last_state = load_last_state()
    changed_products = filter_changed(nuport_products, last_state)

    if not changed_products:
        logging.info("Nothing changed since last sync. Zoho is already up to date.")
        print("\nSync complete: 0 created, 0 updated, 0 stock adjusted, 0 errors\n")
        save_state(nuport_products)
        return

    # ── Step 5: Zoho token + fetch existing items ────────────────────────────
    try:
        token = get_zoho_token(config)
    except Exception as e:
        logging.error(f"FATAL — cannot get Zoho access token: {e}")
        logging.error("Check zoho credentials and zoho_region in config.json")
        sys.exit(1)

    try:
        zoho_lookup = fetch_zoho_items(config, token)
    except Exception as e:
        logging.error(f"FATAL — cannot fetch Zoho items: {e}")
        sys.exit(1)

    # ── Step 6: Update catalogue for changed items only ──────────────────────
    if changed_products:
        logging.info(f"Updating catalogue for {len(changed_products)} changed products...")
        for sku, product in changed_products.items():
            label = f"[{sku}] {product['name']}"
            try:
                if sku in zoho_lookup:
                    existing = zoho_lookup[sku]
                    update_zoho_item(config, token, existing["item_id"], product, existing)
                    logging.info(
                        f"UPDATED  {label}  "
                        f"rate={product['rate']} BDT  purchase={product['purchase_rate']} BDT"
                    )
                    updated += 1
                else:
                    create_zoho_item(config, token, product)
                    logging.info(
                        f"CREATED  {label}  "
                        f"rate={product['rate']} BDT  purchase={product['purchase_rate']} BDT"
                    )
                    created += 1
            except Exception as e:
                logging.error(f"ERROR    {label}  — {e}")
                errors += 1
    else:
        logging.info("No catalogue changes — skipping all Zoho item updates.")

    # ── Step 7: Stock adjustment for ALL products (always fresh vs Zoho) ─────
    logging.info("Calculating stock differences for all products...")
    adjustment_items = []
    for sku, product in nuport_products.items():
        existing_meta = zoho_lookup.get(sku)
        if not existing_meta:
            continue  # new item not yet in Zoho, skip stock
        if existing_meta.get("item_type", "") != "inventory":
            continue  # non-inventory items can't be adjusted
        if existing_meta.get("status", "active") != "active":
            continue  # inactive items can't be adjusted
        qty_diff = product["quantity"] - int(existing_meta["stock_on_hand"])
        if qty_diff != 0:
            adjustment_items.append({
                "item_id":           existing_meta["item_id"],
                "quantity_adjusted": qty_diff,
            })
    logging.info(f"{len(adjustment_items)} items need stock adjustment.")

    # ── Step 7: Sync stock quantities ────────────────────────────────────────
    if adjustment_items:
        try:
            stock_adjusted = sync_zoho_stock(config, token, adjustment_items)
        except Exception as e:
            logging.error(f"Stock adjustment failed: {e}")
            logging.error("Item catalogue was synced successfully. Only stock quantities failed.")

    # ── Step 8: Save state so next run skips unchanged items ─────────────────
    save_state(nuport_products)
    logging.info("State saved — next run will only sync items that change.")

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = (
        f"Sync complete: {created} created, {updated} updated, "
        f"{stock_adjusted} stock adjusted, {errors} errors"
    )
    logging.info("=" * 60)
    logging.info(summary)
    logging.info("=" * 60)
    print(f"\n{summary}\n")


if __name__ == "__main__":
    main()
