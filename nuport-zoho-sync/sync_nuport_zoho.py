#!/usr/bin/env python3
"""
Winterfell Operations — Nuport OMS → Zoho Books inventory sync
Reads all products from Nuport, creates/updates items in Zoho Books.
Match key: SKU. Never deletes anything from Zoho.
"""

import json
import os
import sys
import time
import logging
from datetime import datetime
import urllib.request
import urllib.parse
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


# ── Config ───────────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print("ERROR: config.json not found.")
        print(f"Copy config.example.json to config.json and fill in your credentials.")
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: config.json has invalid JSON: {e}")
        sys.exit(1)


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
    """Call Zoho API with one automatic retry on rate-limit (HTTP 429)."""
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


# ── Step 1: Fetch Nuport inventory ───────────────────────────────────────────

def fetch_nuport_inventory(config):
    """
    Calls GET /integration/inventory?page=-1 which returns ALL records in one shot.
    Each record contains full product details + stock quantity.
    """
    base = config["nuport_base_url"].rstrip("/")
    url = f"{base}/integration/inventory?page=-1"
    headers = {"Authorization": f"Bearer {config['nuport_api_key']}"}

    logging.info("Fetching all inventory from Nuport...")
    try:
        data = http_get(url, headers)
    except Exception as e:
        raise Exception(f"Nuport fetch failed: {e}")

    results = data.get("results", [])
    logging.info(
        f"Nuport returned {len(results)} inventory records "
        f"(API total count: {data.get('count', '?')})"
    )
    return results


# ── Step 2: Parse Nuport records into clean product dicts ────────────────────

def parse_nuport_products(records):
    """
    Build {sku → product_info} from inventory records.
    Skips: deleted products, missing SKU, zero/null price.
    When same SKU appears at multiple locations, keeps the one with most stock.
    """
    products = {}
    skip_no_sku = skip_deleted = skip_bad_price = 0

    for record in records:
        product = record.get("product") or {}

        if product.get("deleted", False):
            skip_deleted += 1
            continue

        sku = (product.get("sku") or "").strip()
        if not sku:
            name = product.get("name", "unnamed")
            logging.warning(f"SKIP (no SKU): '{name}'")
            skip_no_sku += 1
            continue

        # price comes as a string from Nuport (e.g. "350")
        try:
            price = float(product.get("price") or 0)
        except (ValueError, TypeError):
            price = 0.0

        if price <= 0:
            logging.warning(
                f"SKIP (price=0): SKU '{sku}' — '{product.get('name', '')}'"
            )
            skip_bad_price += 1
            continue

        qty = int(record.get("quantity") or 0)

        # Keep the location entry with the highest available stock
        if sku not in products or qty > products[sku]["quantity"]:
            products[sku] = {
                "name":     product.get("name", "").strip(),
                "sku":      sku,
                "rate":     price,
                "quantity": qty,
            }

    logging.info(
        f"Parsed {len(products)} unique valid products  "
        f"(skipped: {skip_deleted} deleted, {skip_no_sku} no-SKU, "
        f"{skip_bad_price} zero-price)"
    )
    return products


# ── Step 3: Fetch existing Zoho Books items ──────────────────────────────────

def fetch_zoho_items(config, token):
    """
    Pages through all Zoho Books items and builds:
        {sku → {item_id, account_id, inventory_account_id}}
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
                }

        page_ctx = data.get("page_context", {})
        if not page_ctx.get("has_more_page", False):
            break
        page += 1

    logging.info(f"Found {len(lookup)} items with SKUs in Zoho Books.")
    return lookup


# ── Step 4: Create item in Zoho Books ───────────────────────────────────────

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
    result = zoho_request("POST", url, payload, headers)
    if result.get("code") != 0:
        raise Exception(f"Zoho rejected create: {result.get('message', result)}")
    return result


# ── Step 5: Update item in Zoho Books ───────────────────────────────────────

def update_zoho_item(config, token, item_id, product, existing):
    """
    Updates name and rate only. Preserves existing account_id and
    inventory_account_id so we don't break Zoho's chart-of-accounts mapping.
    """
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
    if existing.get("account_id"):
        payload["account_id"] = existing["account_id"]
    if existing.get("inventory_account_id"):
        payload["inventory_account_id"] = existing["inventory_account_id"]

    result = zoho_request("PUT", url, payload, headers)
    if result.get("code") != 0:
        raise Exception(f"Zoho rejected update: {result.get('message', result)}")
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    log_file = setup_logging(config.get("log_dir", "logs/"))

    logging.info("=" * 60)
    logging.info("Winterfell  |  Nuport → Zoho Books Sync")
    logging.info(f"Started at  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Log file    {log_file}")
    logging.info("=" * 60)

    created = updated = errors = 0

    # ── Nuport ──────────────────────────────────────────────────────────────
    try:
        records = fetch_nuport_inventory(config)
    except Exception as e:
        logging.error(f"FATAL — cannot reach Nuport: {e}")
        logging.error("Sync aborted. Check nuport_api_key and nuport_base_url in config.json")
        sys.exit(1)

    nuport_products = parse_nuport_products(records)

    if not nuport_products:
        logging.warning("No valid products found in Nuport. Nothing to sync.")
        print("\nSync complete: 0 created, 0 updated, 0 errors")
        return

    # ── Zoho ─────────────────────────────────────────────────────────────────
    try:
        token = get_zoho_token(config)
    except Exception as e:
        logging.error(f"FATAL — cannot get Zoho access token: {e}")
        logging.error(
            "Check zoho_client_id, zoho_client_secret, zoho_refresh_token "
            "and zoho_region in config.json"
        )
        sys.exit(1)

    try:
        zoho_lookup = fetch_zoho_items(config, token)
    except Exception as e:
        logging.error(f"FATAL — cannot fetch Zoho items: {e}")
        sys.exit(1)

    # ── Sync loop ─────────────────────────────────────────────────────────────
    logging.info(f"Syncing {len(nuport_products)} products to Zoho Books...")

    for sku, product in nuport_products.items():
        label = f"[{sku}] {product['name']}"
        try:
            if sku in zoho_lookup:
                existing = zoho_lookup[sku]
                update_zoho_item(config, token, existing["item_id"], product, existing)
                logging.info(f"UPDATED  {label}  @ BDT {product['rate']:.2f}")
                updated += 1
            else:
                create_zoho_item(config, token, product)
                logging.info(f"CREATED  {label}  @ BDT {product['rate']:.2f}")
                created += 1
        except Exception as e:
            logging.error(f"ERROR    {label}  — {e}")
            errors += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = (
        f"Sync complete: {created} created, {updated} updated, "
        f"0 skipped, {errors} errors"
    )
    logging.info("=" * 60)
    logging.info(summary)
    logging.info("=" * 60)
    print(f"\n{summary}\n")


if __name__ == "__main__":
    main()
