============================================================
  WINTERFELL — Nuport Off-Channel Orders → WooCommerce Sync
  Setup Guide (plain English)
============================================================

WHAT THIS DOES
--------------
When a customer orders via WhatsApp, Messenger, Instagram, etc.,
Nuport receives the order but WooCommerce does not. This system
automatically pushes those off-channel Nuport orders into WooCommerce
so your inventory, reports, and fulfilment all stay in sync.

Orders that already came FROM WooCommerce (source = WEBSITE) are
automatically skipped — no duplicates, no double-counting.


FILES IN THIS FOLDER
--------------------
config.json             → Your credentials and settings (DO NOT commit to git)
config.example.json     → Template — copy to config.json and fill in
push_to_woocommerce.py  → Core push logic (filter, map, create WC order)
webhook_listener.py     → Flask server that receives Nuport webhooks
status_sync.py          → Polls Nuport every 15 min and syncs order statuses
test_push.py            → Run this first to verify everything works
requirements.txt        → Python library dependencies
start_listener.bat      → Double-click to run the webhook listener
start_status_sync.bat   → Double-click to run the status sync
setup_scheduler.bat     → Run as Admin to register both as Windows services
tracked_orders.json     → Auto-created: tracks SO → WC order ID mappings
failed_orders.json      → Auto-created: orders that failed, retried on next run
logs/                   → Daily log files (sync_2026-05-15.log etc.)


STEP 1 — INSTALL PYTHON
------------------------
Download Python 3.10 or newer from: https://www.python.org/downloads/
IMPORTANT: Tick "Add Python to PATH" during install.


STEP 2 — INSTALL DEPENDENCIES
-------------------------------
Open Command Prompt in this folder, run:
  pip install -r requirements.txt


STEP 3 — VERIFY CONFIG
-----------------------
Open config.json and confirm:
  - nuport_api_key        your Nuport API key (Settings → API in Nuport)
  - woocommerce_url       https://winterfellbd.com
  - woocommerce_consumer_key / woocommerce_consumer_secret
                          WooCommerce → Settings → Advanced → REST API → Add Key
  - nuport_webhook_secret any long random string (you choose this, then set the
                          same value in Nuport's webhook settings)

IMPORTANT NOTE about "website_order_id_field":
  The config.json has: "website_order_id_field": "integrationId"
  This tells the system which Nuport field holds the WooCommerce order ID
  for orders that originated from WooCommerce.
  To verify this is the right field:
    1. In Nuport, find an order with Source = "Website" (a WooCommerce order)
    2. Note the Website Order ID shown in Nuport (e.g. 76106)
    3. Run: python verify_fields.py --so-number SO-XXXXX
    4. If integrationId shows that number → you are set
    5. If referenceId shows it instead → change "website_order_id_field"
       to "referenceId" in config.json


STEP 4 — RUN THE TEST FIRST
-----------------------------
In Command Prompt:
  python test_push.py --dry-run        (shows what would be sent, no WC call)
  python test_push.py                  (actually pushes a test order to WC)

After running test_push.py:
  - Go to WooCommerce → Orders
  - Look for order from "Zahid Mustafiz" with note "Order via WHATSAPP | Nuport: SO-TEST99"
  - If it appears: everything is working!
  - If not: check logs/sync_<today>.log for the error

After confirming the test order appeared, delete it from WooCommerce.


STEP 5 — SET UP THE WEBHOOK (FOR LIVE ORDERS)
----------------------------------------------
You need a public URL so Nuport can reach your computer.

Option A — ngrok (free, easiest for testing):
  1. Download ngrok from https://ngrok.com and sign up (free)
  2. Double-click start_listener.bat to start the listener
  3. In another Command Prompt window:
       ngrok http 5000
  4. ngrok will show a URL like: https://abc123.ngrok.io
  5. Log in to Nuport → Settings → Webhooks
  6. Add webhook URL: https://abc123.ngrok.io/webhook/nuport
  7. Set secret: the same value as nuport_webhook_secret in config.json
  Note: ngrok URL changes each time you restart. For permanent setup, use a fixed URL.

Option B — Server hosting (permanent):
  Deploy these Python files to your WooCommerce web server (ask your host for
  SSH or cPanel Python access). Set the listener to run permanently.
  The webhook URL becomes: https://winterfellbd.com:5000/webhook/nuport
  (or configure nginx/Apache to proxy port 5000)


STEP 6 — SET UP AUTOMATIC STARTUP (WINDOWS)
---------------------------------------------
If running on an always-on Windows PC:
  1. Right-click setup_scheduler.bat → Run as administrator
  2. Both services will now start automatically after login
  OR: Double-click start_listener.bat and start_status_sync.bat manually


WHAT HAPPENS WHEN AN ORDER COMES IN
-------------------------------------
1. Customer places order on WhatsApp/Messenger/Instagram with your team
2. Your team enters it into Nuport
3. Nuport fires a webhook to your listener
4. Listener (webhook_listener.py) receives it, returns HTTP 200 immediately
5. In the background:
   - Checks: is this a WEBSITE/WooCommerce order? If yes → skip
   - Checks: is this SO number already in WooCommerce? If yes → skip
   - Looks up the customer by phone in WooCommerce
   - Looks up each product by SKU in WooCommerce
   - Creates the WooCommerce order with correct status, billing, line items
   - Saves the SO → WC order ID mapping to tracked_orders.json
6. Every 15 minutes, status_sync.py checks if status changed in Nuport
   and updates the matching WooCommerce order


STATUS MAPPING (Nuport → WooCommerce)
---------------------------------------
Nuport Status       WooCommerce Status
Pending             processing
Approved            processing
Processing          processing
Shipped             processing
In-Transit          processing
On Hold             on-hold
Delivered           pending    (COD not yet collected)
Payment_Collected   completed
Cancelled           cancelled
Flagged_Returned    refunded
Flagged_Damaged     refunded
[unknown]           on-hold   (+ warning in log)


LOG FILES
----------
A new log file is created each day in the logs/ folder.
Example: logs/sync_2026-05-15.log

Each line shows:
  timestamp  [LEVEL]  message

Important log entries to watch for:
  OK SO-XXXXX → WC order #12345    ← success
  SKIP SO-XXXXX: source is 'WEBSITE' ← correctly skipped
  SKIP SO-XXXXX: duplicate blocked  ← duplicate caught
  FAIL SO-XXXXX — queued for retry  ← failed, will retry
  Unknown Nuport status 'XXX'       ← new status not in mapping table


TROUBLESHOOTING
---------------
"Module not found" error
  → Run: pip install -r requirements.txt

Orders not appearing in WooCommerce
  → Check logs/ for errors
  → Verify config.json has correct WC credentials
  → Test WC connection: python test_push.py

WooCommerce API 401 error
  → Your Consumer Key or Secret is wrong
  → Regenerate at: WooCommerce → Settings → Advanced → REST API

Nuport webhook not arriving
  → Check your ngrok/server URL is correct in Nuport settings
  → Check the listener is running (start_listener.bat)
  → Test manually: curl -X POST http://localhost:5000/health

SKUs not matching
  → If Nuport SKU doesn't match WooCommerce SKU, the order is still created
    but uses product name + price only. Check for "SKU not in WC" in logs.

Wrong orders being pushed/skipped
  → The "website_order_id_field" may be wrong — see STEP 3 note above
  → Check source values: must be UPPERCASE in the API (WEBSITE, WHATSAPP, etc.)


============================================================
  SUPPORT: check logs/ first, then check config.json values
============================================================
