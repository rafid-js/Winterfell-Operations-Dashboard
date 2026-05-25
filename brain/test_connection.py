import sys
from sqlalchemy import text

try:
    from db import get_connection
except RuntimeError as e:
    print(f"✗ Config error: {e}")
    sys.exit(1)

try:
    with get_connection() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        ts = conn.execute(text("SELECT NOW()")).scalar()
        print("✓ Connected to Winterfell Brain successfully")
        print(f"✓ PostgreSQL: {version.split(',')[0].strip()}")
        print(f"✓ Server time: {ts}")
except Exception as e:
    print(f"✗ Connection failed: {type(e).__name__}: {e}")
    print()
    print("To fix:")
    print("  1. Check DATABASE_URL in .env matches Railway dashboard exactly")
    print("  2. Make sure the Railway PostgreSQL service is running (not sleeping)")
    print("  3. Verify the host/port are accessible from your network")
    sys.exit(1)
