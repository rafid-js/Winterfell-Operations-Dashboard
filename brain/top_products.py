"""Top products report — delivered orders, all sizes combined."""
import sys
import argparse
sys.path.insert(0, __file__.rsplit('\\', 1)[0])
sys.path.insert(0, __file__.rsplit('/', 1)[0])

from db import get_connection
from sqlalchemy import text

DELIVERED = "('DELIVERED', 'Delivered', 'delivered', 'COMPLETED')"

BASE_NAME = r"""
    TRIM(regexp_replace(
        COALESCE(s.product_name, oi.product_name),
        '\s*-\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[23][0-9])\s*$',
        '',
        'i'
    ))
"""


def top_products(conn, limit: int, days: int = None) -> list:
    date_filter = f"AND o.order_date >= NOW() - INTERVAL '{days} days'" if days else ""
    return conn.execute(text(f"""
        SELECT
            {BASE_NAME} AS base_name,
            SUM(oi.quantity)                AS qty_sold,
            COUNT(DISTINCT o.so_number)     AS orders,
            COALESCE(SUM(oi.total_price), 0) AS revenue
        FROM order_items oi
        JOIN orders o ON oi.so_number = o.so_number
        LEFT JOIN skus s ON oi.sku = s.sku
        WHERE o.nuport_status IN {DELIVERED}
          {date_filter}
        GROUP BY base_name
        ORDER BY qty_sold DESC
        LIMIT {limit}
    """)).fetchall()


def print_report(title: str, rows: list):
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")
    if not rows:
        print("  No data.")
        return
    print(f"  {'RANK':>4}  {'QTY':>6}  {'ORDERS':>6}  {'REVENUE (৳)':>14}  NAME")
    print(f"  {'-' * 74}")
    for i, r in enumerate(rows, 1):
        print(f"  {i:>4}  {r[1]:>6}  {r[2]:>6}  {r[3]:>14,.0f}  {r[0] or '-'}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Top products report')
    ap.add_argument('--limit', type=int, default=None,
                    help='Override row limit for all reports (default: varies per report)')
    ap.add_argument('--days',  type=int, default=None,
                    help='Filter to last N days only (overrides preset periods)')
    args = ap.parse_args()

    with get_connection() as conn:

        if args.days or args.limit:
            # Custom one-off query
            lim = args.limit or 50
            label = f"last {args.days} days" if args.days else "all time"
            print_report(f"Top {lim} Products — {label}", top_products(conn, lim, args.days))

        else:
            # Full suite of reports
            print_report("Top 50 Products — All Time",          top_products(conn, 50))
            print_report("Top 100 Products — All Time",         top_products(conn, 100))
            print_report("Top 50 Products — Last 3 Months",     top_products(conn, 50,  days=90))
            print_report("Top 50 Products — Last 1 Month",      top_products(conn, 50,  days=30))
            print_report("Top 20 Products — Last 7 Days",       top_products(conn, 20,  days=7))

    print()
