================================================================
WINTERFELL BRAIN — README
Single-source PostgreSQL database on Railway.app
================================================================

1. WHAT IS THE WINTERFELL BRAIN?
---------------------------------
The Brain is a PostgreSQL database hosted on Railway.app that acts
as the single source of truth for your entire business. Every system
you run — WooCommerce, Nuport, Zoho Books, Pathao, Meta Ads,
Inventory — writes into and reads from this one database.

It stores 8 core tables:
  • customers       — every customer, unified by phone number
  • orders          — every SO from Nuport, linked to WC + Zoho + Pathao
  • financials      — invoice vs. Pathao payout reconciliation
  • pathao_waybills — full delivery tracking + loss/anomaly detection
  • skus            — product catalogue + live stock levels
  • ad_spend        — Meta Ads daily spend with true ROAS calculation
  • knowledge_base  — WhatsApp, emails, docs with AI vector search
  • alerts_log      — all system alerts (stockout, loss, mismatch)

It also has 4 pre-built views:
  • daily_revenue         — revenue by day and channel
  • inventory_health      — stock status with reorder signals
  • pathao_loss_tracker   — all anomalies and lost parcel records
  • true_roas_by_campaign — actual ROAS vs. Meta's claimed numbers


2. FINDING YOUR DATABASE_URL ON RAILWAY
-----------------------------------------
1. Go to https://railway.app and log in
2. Open your project
3. Click on the PostgreSQL service (purple elephant icon)
4. Click the "Variables" tab
5. Find DATABASE_URL and click the copy icon

It looks like:
  postgresql://postgres:PASSWORD@HOST:PORT/railway


3. FILLING IN THE .env FILE
-----------------------------
Open brain/.env and paste your DATABASE_URL on the first line.
Fill in the other credentials as you connect each system.
NEVER commit .env to git — it's in .gitignore.


4. RUNNING setup_windows.bat (Windows only)
---------------------------------------------
Double-click setup_windows.bat (or right-click → Run as Administrator)
This installs all required Python packages:
  psycopg2-binary, sqlalchemy, pgvector, python-dotenv


5. RUNNING test_connection.py
-------------------------------
From the project root:
  cd brain
  python test_connection.py

Expected output:
  ✓ Connected to Winterfell Brain successfully
  ✓ PostgreSQL: PostgreSQL 15.x ...
  ✓ Server time: 2026-05-25 ...


6. RUNNING create_tables.py
-----------------------------
  python create_tables.py

This creates all 8 tables, 15+ indexes, and 4 views.
Safe to re-run — uses IF NOT EXISTS throughout.


7. RUNNING health_check.py
----------------------------
  python health_check.py

Run this any time to verify the Brain is healthy.
Shows: connection status, extensions, table row counts,
latest order, latest reconciliation, active alerts.


8. CONNECTING FUTURE SCRIPTS
------------------------------
Every new script starts with just two lines:

  from db import get_connection
  from sqlalchemy import text

  with get_connection() as conn:
      result = conn.execute(text("SELECT * FROM orders LIMIT 10"))

That's it. db.py handles credentials, pooling, and reconnects.


9. RAILWAY FREE TIER LIMITS
-----------------------------
Free tier (Hobby plan, no credit card):
  • 500 MB PostgreSQL storage
  • $5 of compute credits/month
  • Sleeps after inactivity (wakes on first connection ~2s delay)

When to upgrade to Pro ($5/month):
  • Approaching 500 MB storage (check with: SELECT pg_size_pretty(pg_database_size('railway')))
  • Need 24/7 uptime without sleep
  • Running scheduled jobs (syncs, reconciliation crons)

Typical growth: ~1 MB per 1,000 orders. At 10k orders/month
you'll use ~120 MB/year — well within free tier for a long time.

================================================================
