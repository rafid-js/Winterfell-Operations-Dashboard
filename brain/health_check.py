import sys
from sqlalchemy import text
from datetime import datetime

try:
    from db import get_connection
except RuntimeError as e:
    print(f"✗ Config error: {e}")
    sys.exit(1)

TABLES = [
    "customers", "orders", "financials", "pathao_waybills",
    "skus", "ad_spend", "knowledge_base", "alerts_log",
]

VIEWS = [
    "daily_revenue", "inventory_health",
    "pathao_loss_tracker", "true_roas_by_campaign",
]


def main():
    print("=== Winterfell Brain — Health Check ===")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        with get_connection() as conn:

            print("✓ Database connection: OK\n")

            # ── Extensions ─────────────────────────────────────────
            exts = conn.execute(text(
                "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')"
            )).fetchall()
            ext_names = {r[0] for r in exts}
            print("Extensions:")
            for ext, label in [("vector", "pgvector"), ("pg_trgm", "pg_trgm")]:
                mark = "✓" if ext in ext_names else "✗"
                status = "installed" if ext in ext_names else "NOT installed"
                print(f"  {mark} {label:<12} {status}")

            # ── Tables ─────────────────────────────────────────────
            print("\nTables:")
            tables_ok = 0
            for table in TABLES:
                try:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                    print(f"  ✓ {table:<22} {count:>6} rows")
                    tables_ok += 1
                except Exception:
                    print(f"  ✗ {table:<22} NOT FOUND")
            print(f"  → {tables_ok}/{len(TABLES)} tables present")

            # ── Views ──────────────────────────────────────────────
            print("\nViews:")
            views_ok = 0
            for view in VIEWS:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.views "
                    "WHERE table_name = :v AND table_schema = 'public'"
                ), {"v": view}).fetchone()
                mark = "✓" if exists else "✗"
                status = "OK" if exists else "MISSING"
                print(f"  {mark} {view:<28} {status}")
                if exists:
                    views_ok += 1
            print(f"  → {views_ok}/{len(VIEWS)} views present")

            # ── Latest activity ────────────────────────────────────
            print("\nLatest activity:")
            try:
                row = conn.execute(text(
                    "SELECT so_number, created_at FROM orders ORDER BY created_at DESC LIMIT 1"
                )).fetchone()
                if row:
                    print(f"  ✓ Latest order ingested:       {row[0]} at {row[1]}")
                else:
                    print("  ⚪ Latest order ingested:       no data yet")
            except Exception as e:
                print(f"  ✗ orders query: {e}")

            try:
                row = conn.execute(text(
                    "SELECT reconciled_at FROM financials "
                    "WHERE reconciled = TRUE ORDER BY reconciled_at DESC LIMIT 1"
                )).fetchone()
                if row and row[0]:
                    print(f"  ✓ Latest reconciliation run:   {row[0]}")
                else:
                    print("  ⚪ Latest reconciliation run:   none yet")
            except Exception as e:
                print(f"  ✗ financials query: {e}")

            try:
                count = conn.execute(text(
                    "SELECT COUNT(*) FROM alerts_log WHERE created_at >= CURRENT_DATE"
                )).scalar()
                print(f"  ✓ Anomaly alerts today:        {count}")
            except Exception as e:
                print(f"  ✗ alerts_log query: {e}")

            try:
                row = conn.execute(text(
                    "SELECT COUNT(*), COALESCE(SUM(loss_value), 0) "
                    "FROM pathao_waybills WHERE is_lost = TRUE"
                )).fetchone()
                print(f"  ✓ Lost parcels tracked:        {row[0]} (BDT {row[1]:,.2f})")
            except Exception as e:
                print(f"  ✗ pathao_waybills query: {e}")

            # ── Summary ────────────────────────────────────────────
            all_good = (tables_ok == len(TABLES) and views_ok == len(VIEWS))
            print()
            if all_good:
                print("✓ Brain status: HEALTHY")
            else:
                print("⚠ Brain status: NEEDS ATTENTION (see above)")

    except Exception as e:
        print(f"✗ Connection failed: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
