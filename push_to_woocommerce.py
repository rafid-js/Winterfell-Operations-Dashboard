"""
push_to_woocommerce.py
Nuport off-channel orders → WooCommerce

CONFIRMED NUPORT API FIELD NAMES (verified against live order SO-65778):
  order["internalId"]                    → SO number e.g. "SO-65778"
  order["source"]                        → UPPERCASE: "WEBSITE", "WHATSAPP", etc.
  order["referenceId"]                   → WC order ID e.g. "76106" ✓ CONFIRMED
  order["integrationId"]                 → Nuport-internal UUID, NOT the WC order ID
  order["status"]                        → "REQUESTED" for Pending (not "PENDING") ✓ CONFIRMED
                                           Full map: REQUESTED/APPROVED/PROCESSING/SHIPPED/
                                           IN_TRANSIT/ON_HOLD/COMPLETED/CANCELLED/FLAGGED
  order["deliveryCharge"]                → string e.g. "80"
  order["distributor"]["name"]           → customer full name
  order["distributor"]["phone"]          → "+8801XXXXXXXXX"
  order["distributor"]["email"]          → may be null
  order["location"]["address"]           → delivery address string
  order["location"]["district"]          → "Dhaka District"
  order["location"]["postCode"]          → postcode string
  order["salesOrderItems"][]["quantity"] → int
  order["salesOrderItems"][]["price"]    → string price
  order["salesOrderItems"][]["product"]["sku"]  → SKU string
  order["salesOrderItems"][]["product"]["name"] → product name
"""

import json
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

_config_cache = None
TRACKED_ORDERS_FILE = Path(__file__).parent / "tracked_orders.json"
FAILED_ORDERS_FILE = Path(__file__).parent / "failed_orders.json"

# ── Config ────────────────────────────────────────────────────────────────────

def get_config() -> dict:
    global _config_cache
    if _config_cache is None:
        cfg_path = Path(__file__).parent / "config.json"
        with open(cfg_path, encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def setup_logging():
    cfg = get_config()
    log_dir = Path(cfg.get("log_dir", "logs/"))
    log_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"sync_{today}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


# ── Status mapping ────────────────────────────────────────────────────────────
# Nuport API status values are UPPERCASE strings.
# The UI shows them with spaces/underscores — the API uses these exact strings.
STATUS_MAP = {
    # Active / in-progress → WC "processing"
    "PENDING":           "processing",
    "REQUESTED":         "processing",   # alternate name seen in some accounts
    "APPROVED":          "processing",
    "PROCESSING":        "processing",
    "SHIPPED":           "processing",
    "IN_TRANSIT":        "processing",
    # On hold
    "ON_HOLD":           "on-hold",
    # Delivered but COD not yet collected → WC "pending" (awaiting payment)
    "COMPLETED":         "pending",      # Nuport "Delivered" display name
    "DELIVERED":         "pending",
    "DELIVERY_DUE":      "pending",
    "PAYMENT_DUE":       "pending",
    # COD collected → WC "completed"
    "PAYMENT_COLLECTED": "completed",
    # Cancelled
    "CANCELLED":         "cancelled",
    "CANCELED":          "cancelled",
    # Returned / damaged → WC "refunded"
    "FLAGGED":           "refunded",
    "RETURNED":          "refunded",
    "DAMAGED":           "refunded",
    "FLAGGED_RETURNED":  "refunded",
    "FLAGGED_DAMAGED":   "refunded",
}


def map_status(nuport_status: str) -> str:
    key = (nuport_status or "").upper().replace(" ", "_").replace("-", "_")
    if key not in STATUS_MAP:
        logging.warning(f"Unknown Nuport status '{nuport_status}' — mapping to 'on-hold'")
        return "on-hold"
    return STATUS_MAP[key]


# ── WooCommerce helpers ───────────────────────────────────────────────────────

def _wc_auth() -> HTTPBasicAuth:
    cfg = get_config()
    return HTTPBasicAuth(cfg["woocommerce_consumer_key"], cfg["woocommerce_consumer_secret"])


def _wc_url(path: str) -> str:
    cfg = get_config()
    base = cfg["woocommerce_url"].rstrip("/")
    return f"{base}/wp-json/wc/v3{path}"


# ── Phone normalisation ───────────────────────────────────────────────────────

def normalise_phone(raw: str) -> str:
    """Strip +880 country code and return local 01XXXXXXXXX format."""
    if not raw:
        return ""
    p = re.sub(r"[\s\-\(\)]", "", raw)
    if p.startswith("+880"):
        p = "0" + p[4:]
    elif p.startswith("880") and len(p) >= 13:
        p = "0" + p[3:]
    return p


# ── Email validation ──────────────────────────────────────────────────────────

def sanitise_email(raw: str) -> str:
    """Return email if valid, else '' — WC accepts '' but rejects invalid formats."""
    if not raw:
        return ""
    import re as _re
    if _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', raw.strip()):
        return raw.strip()
    logging.warning(f"Invalid email discarded: '{raw}'")
    return ""



def split_name(full_name: str) -> tuple:
    parts = (full_name or "").strip().split(None, 1)
    first = parts[0] if parts else "Customer"
    last = parts[1] if len(parts) > 1 else ""
    return first, last


# ── City extraction ───────────────────────────────────────────────────────────

def extract_city(district: str, address: str) -> str:
    if district:
        city = district.replace(" District", "").replace(" district", "").strip()
        if city:
            return city
    if address:
        parts = [p.strip() for p in re.split(r"[,\n]", address) if p.strip()]
        for part in reversed(parts):
            clean = part.strip()
            if clean.upper() not in ("BD", "BANGLADESH") and not clean.isdigit():
                return clean
    return "Dhaka"


# ── Customer lookup ───────────────────────────────────────────────────────────

def lookup_customer(phone: str):
    """Search WooCommerce for an existing customer by phone. Returns customer_id or None."""
    if not phone:
        return None
    normalised = normalise_phone(phone)
    if not normalised:
        return None
    try:
        resp = requests.get(
            _wc_url("/customers"),
            params={"search": normalised},
            auth=_wc_auth(),
            timeout=10,
        )
        resp.raise_for_status()
        customers = resp.json()
        if customers:
            return customers[0]["id"]
    except Exception as exc:
        logging.warning(f"Customer lookup failed for {normalised}: {exc}")
    return None


# ── Product lookup by SKU ─────────────────────────────────────────────────────

def lookup_product(sku: str) -> tuple:
    """
    Search WooCommerce products by SKU.
    Returns (product_id, variation_id).
    variation_id is None for simple products; set for variable product variations.
    """
    if not sku:
        return None, None
    try:
        resp = requests.get(
            _wc_url("/products"),
            params={"sku": sku},
            auth=_wc_auth(),
            timeout=10,
        )
        resp.raise_for_status()
        products = resp.json()
        if products:
            p = products[0]
            parent_id = p.get("parent_id")
            if parent_id and parent_id != 0:
                return parent_id, p["id"]
            return p["id"], None
    except Exception as exc:
        logging.warning(f"Product lookup failed for SKU '{sku}': {exc}")
    return None, None


# ── Duplicate check (Layer 2) ─────────────────────────────────────────────────

def is_duplicate_in_wc(so_number: str) -> bool:
    """
    Layer 2 duplicate check using local tracked_orders.json.
    Written to on every successful push — prevents double-push if webhook fires twice.
    WooCommerce REST API does not support meta_key/meta_value filtering natively,
    so local tracking is the reliable approach.
    """
    return so_number in load_tracked_orders()


# ── Filter logic ──────────────────────────────────────────────────────────────

def check_should_push(order: dict) -> tuple:
    """
    Returns (should_push: bool, reason: str).
    Applies both skip conditions before deciding to push.
    """
    cfg = get_config()
    so_number = order.get("internalId", "?")

    # Layer 1 — Website Order ID check (fastest)
    # CONFIRMED from live API (SO-65778): referenceId = "76106" (WC order #76106).
    # integrationId is a Nuport-internal UUID — not the WC order ID.
    reference_id = order.get("referenceId")
    if reference_id:
        return False, f"referenceId '{reference_id}' present — order already in WooCommerce"

    source = (order.get("source") or "").upper()
    skip_sources = {s.upper() for s in cfg.get("skip_sources", ["WEBSITE"])}
    push_sources = {s.upper() for s in cfg.get("push_sources", [])}

    if source in skip_sources:
        return False, f"source '{source}' is in skip list"

    if source in push_sources:
        return True, f"source '{source}' is in push list"

    # Unknown source — push as precaution, log warning
    logging.warning(f"{so_number}: Unknown source [{source}] — pushed as precaution")
    return True, f"unknown source '{source}' — pushed as precaution"


# ── Build WooCommerce order payload ──────────────────────────────────────────

def build_wc_order(nuport_order: dict, customer_id=None) -> dict:
    so_number = nuport_order.get("internalId", "")
    source = nuport_order.get("source", "")
    wc_status = map_status(nuport_order.get("status", ""))

    distributor = nuport_order.get("distributor") or {}
    location = nuport_order.get("location") or {}

    full_name = distributor.get("name", "")
    first_name, last_name = split_name(full_name)
    phone = normalise_phone(distributor.get("phone", ""))
    email = sanitise_email(distributor.get("email") or "")
    address = location.get("address", "")
    district = location.get("district", "")
    city = extract_city(district, address)
    postcode = location.get("postCode", "") or ""

    # Build line items
    line_items = []
    skus_missing = []
    for item in nuport_order.get("salesOrderItems", []):
        product = item.get("product") or {}
        sku = product.get("sku", "")
        product_name = product.get("name", "")
        quantity = int(item.get("quantity", 1))
        price = float(item.get("price", 0) or 0)

        product_id, variation_id = lookup_product(sku)

        li = {
            "quantity": quantity,
            "subtotal": f"{price * quantity:.2f}",
            "total": f"{price * quantity:.2f}",
        }

        if product_id:
            li["product_id"] = product_id
            if variation_id:
                li["variation_id"] = variation_id
        else:
            if sku:
                skus_missing.append(sku)
                logging.warning(f"{so_number}: SKU not in WC: '{sku}' — using name+price")
            li["name"] = product_name
            li["price"] = f"{price:.2f}"

        line_items.append(li)

    delivery_charge = float(nuport_order.get("deliveryCharge", 0) or 0)

    sku_note = f" | Missing SKUs: {', '.join(skus_missing)}" if skus_missing else ""
    customer_note = f"Order via {source} | Nuport: {so_number}{sku_note}"

    billing = {
        "first_name": first_name,
        "last_name": last_name,
        "address_1": address,
        "address_2": "",
        "city": city,
        "postcode": postcode,
        "country": "BD",
        "phone": phone,
    }
    if email:
        billing["email"] = email  # Omit entirely if invalid — WC rejects both bad values and ""

    payload = {
        "status": wc_status,
        "payment_method": "cod",
        "payment_method_title": "Cash on Delivery",
        "currency": "BDT",
        "billing": billing,
        "shipping": {
            "first_name": first_name,
            "last_name": last_name,
            "address_1": address,
            "address_2": "",
            "city": city,
            "postcode": postcode,
            "country": "BD",
        },
        "line_items": line_items,
        "shipping_lines": [
            {
                "method_title": "Delivery Fee",
                "method_id": "flat_rate",
                "total": f"{delivery_charge:.2f}",
            }
        ],
        "meta_data": [
            {"key": "_nuport_so_number",    "value": so_number},
            {"key": "_nuport_order_source", "value": source},
            {"key": "_nuport_synced_at",    "value": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")},
        ],
        "customer_note": customer_note,
    }

    if customer_id:
        payload["customer_id"] = customer_id

    return payload


# ── Create WC order ───────────────────────────────────────────────────────────

def create_wc_order(payload: dict):
    """POST order to WooCommerce REST API. Returns the created order dict or None."""
    try:
        resp = requests.post(
            _wc_url("/orders"),
            json=payload,
            auth=_wc_auth(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:500] if exc.response is not None else ""
        logging.error(f"WC API HTTP error: {exc} — {body}")
        return None
    except Exception as exc:
        logging.error(f"WC API error: {exc}")
        return None


# ── Order tracking (for status_sync) ─────────────────────────────────────────

def load_tracked_orders() -> dict:
    if TRACKED_ORDERS_FILE.exists():
        try:
            with open(TRACKED_ORDERS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_tracked_orders(orders: dict):
    with open(TRACKED_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)


def add_tracked_order(so_number: str, wc_order_id: int, wc_status: str):
    orders = load_tracked_orders()
    orders[so_number] = {
        "wc_order_id": wc_order_id,
        "last_wc_status": wc_status,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_tracked_orders(orders)


# ── Failed order queue ────────────────────────────────────────────────────────

def queue_failed_order(nuport_order: dict, reason: str):
    orders = []
    if FAILED_ORDERS_FILE.exists():
        try:
            with open(FAILED_ORDERS_FILE, encoding="utf-8") as f:
                orders = json.load(f)
        except Exception:
            pass
    orders.append({
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "order": nuport_order,
    })
    with open(FAILED_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)
    logging.warning(f"Order {nuport_order.get('internalId', '?')} saved to failed_orders.json")


def retry_failed_orders():
    """Re-attempt any orders that failed on a previous webhook call."""
    if not FAILED_ORDERS_FILE.exists():
        return
    try:
        with open(FAILED_ORDERS_FILE, encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:
        return

    if not entries:
        return

    logging.info(f"Retrying {len(entries)} failed order(s)...")
    remaining = []
    for entry in entries:
        success = process_order(entry["order"])
        if not success:
            remaining.append(entry)

    with open(FAILED_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining, f, indent=2)

    if remaining:
        logging.warning(f"{len(remaining)} order(s) still failed after retry")
    else:
        logging.info("All previously failed orders pushed successfully")


# ── Alert email ───────────────────────────────────────────────────────────────

def send_alert_email(subject: str, body: str):
    cfg = get_config()
    alert_email = cfg.get("alert_email", "")
    if not alert_email:
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = alert_email
        msg["To"] = alert_email
        with smtplib.SMTP("localhost") as smtp:
            smtp.send_message(msg)
    except Exception as exc:
        logging.warning(f"Alert email failed: {exc}")


# ── Main entry point ──────────────────────────────────────────────────────────

def process_order(nuport_order: dict) -> bool:
    """
    Full pipeline: filter → duplicate check → build → push.
    Returns True if the order was handled (pushed or skipped), False if it failed.
    """
    so_number = nuport_order.get("internalId", "?")

    should_push, reason = check_should_push(nuport_order)
    if not should_push:
        logging.info(f"SKIP {so_number}: {reason}")
        return True

    logging.info(f"PUSH {so_number}: {reason}")

    # Layer 2 — WC meta search duplicate check
    if is_duplicate_in_wc(so_number):
        logging.info(f"SKIP {so_number}: duplicate blocked — already exists in WooCommerce")
        return True

    customer_id = lookup_customer(
        (nuport_order.get("distributor") or {}).get("phone", "")
    )

    payload = build_wc_order(nuport_order, customer_id)
    result = create_wc_order(payload)

    if result:
        wc_id = result.get("id", "?")
        wc_status = result.get("status", "?")
        logging.info(f"OK {so_number} → WC order #{wc_id} (status: {wc_status})")
        add_tracked_order(so_number, wc_id, wc_status)
        return True
    else:
        logging.error(f"FAIL {so_number} — queued for retry")
        queue_failed_order(nuport_order, "create_wc_order returned None")
        send_alert_email(
            f"[Winterfell Sync] FAILED: {so_number}",
            f"Order {so_number} could not be pushed to WooCommerce.\nCheck logs/ for details.",
        )
        return False
