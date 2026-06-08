"""Quick report: top products by quantity sold (delivered orders only)."""
import sys
sys.path.insert(0, __file__.rsplit('\\', 1)[0])
sys.path.insert(0, __file__.rsplit('/', 1)[0])

from db import get_connection
from sqlalchemy import text

with get_connection() as conn:

    print("=== Order statuses in DB ===")
    rows = conn.execute(text(
        "SELECT nuport_status, COUNT(*) AS n FROM orders GROUP BY nuport_status ORDER BY n DESC"
    )).fetchall()
    for r in rows:
        print(f"  {r[1]:>8}  {r[0]}")

    print("\n=== Top 20 Products by quantity sold — all sizes combined (Delivered) ===\n")
    rows = conn.execute(text(r"""
        SELECT
            TRIM(regexp_replace(
                COALESCE(s.product_name, oi.product_name),
                '\s*-\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[23][0-9])\s*$',
                '',
                'i'
            )) AS base_name,
            SUM(oi.quantity)            AS qty_sold,
            COUNT(DISTINCT o.so_number) AS orders,
            COALESCE(SUM(oi.total_price), 0) AS revenue
        FROM order_items oi
        JOIN orders o ON oi.so_number = o.so_number
        LEFT JOIN skus s ON oi.sku = s.sku
        WHERE o.nuport_status IN ('DELIVERED', 'Delivered', 'delivered')
        GROUP BY base_name
        ORDER BY qty_sold DESC
        LIMIT 20
    """)).fetchall()

    if not rows:
        print("No data found. Check the status name above and edit this file if needed.")
    else:
        print(f"{'QTY':>6}  {'ORDERS':>6}  {'REVENUE':>12}  NAME")
        print("-" * 80)
        for r in rows:
            print(f"{r[1]:>6}  {r[2]:>6}  {r[3]:>12,.0f}  {r[0] or '-'}")
