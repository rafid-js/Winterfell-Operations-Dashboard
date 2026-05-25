"""
Wipes partial import data and reclaims disk space.
Run once after a failed bulk import before re-importing.
"""
import sys
from sqlalchemy import text
from db import get_connection

def main():
    print("=== Brain — Cleanup ===\n")

    print("Truncating partial data...")
    with get_connection() as conn:
        conn.execute(text(
            "TRUNCATE order_items, financials, pathao_waybills, orders, customers CASCADE"
        ))
        conn.commit()
        print("  ✓ All tables wiped\n")

    print("Running VACUUM FULL (reclaims disk space)...")
    print("  This may take a few minutes...\n")

    # VACUUM FULL must run outside a transaction
    import psycopg2
    from dotenv import load_dotenv
    import os
    load_dotenv()
    raw = psycopg2.connect(os.getenv('DATABASE_URL'))
    raw.autocommit = True
    cur = raw.cursor()
    cur.execute("VACUUM FULL")
    cur.close()
    raw.close()
    print("  ✓ VACUUM FULL complete\n")

    print("✓ Database cleaned and space reclaimed.\n")
    print("Now run:")
    print("  python -m sync.alter_tables")
    print("  python -m sync.nuport_csv_import --file \"C:\\Users\\nuport-orders.csv\"")

if __name__ == '__main__':
    main()
