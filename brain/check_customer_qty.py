"""
Diagnostic: show per-order item breakdown for a specific customer.
Usage:  python check_customer_qty.py +8801711542420
"""
import sys
sys.path.insert(0, '.')
from db import get_connection
from sqlalchemy import text

phone = sys.argv[1] if len(sys.argv) > 1 else '+8801711542420'

with get_connection() as conn:

    # Per-order breakdown
    rows = conn.execute(text("""
        SELECT
            o.so_number,
            o.order_date::date          AS date,
            o.nuport_status             AS status,
            o.product_total             AS total,
            COALESCE(SUM(oi.quantity), 0) AS qty,
            COUNT(oi.sku)               AS line_items
        FROM orders o
        LEFT JOIN order_items oi ON o.so_number = oi.so_number
        WHERE o.customer_phone = :phone
          AND o.nuport_status IN ('DELIVERED','Delivered','delivered','COMPLETED')
          AND o.so_number ~ '^(SO|WIN)-[0-9]+$'
        GROUP BY o.so_number, o.order_date, o.nuport_status, o.product_total
        ORDER BY o.order_date DESC
    """), {'phone': phone}).fetchall()

    print(f"\nDelivered orders for {phone}\n")
    print(f"  {'SO Number':<22} {'Date':<12} {'Total':>8}  {'Qty':>5}  {'Lines':>5}")
    print(f"  {'-'*60}")
    total_qty = 0
    for r in rows:
        total_qty += int(r[4])
        print(f"  {r[0]:<22} {str(r[1]):<12} {r[3]:>8,.0f}  {int(r[4]):>5}  {int(r[5]):>5}")
    print(f"  {'-'*60}")
    print(f"  {'TOTAL':<22} {'':<12} {'':>8}  {total_qty:>5}\n")

    # Now show items for any order with qty > 5
    suspicious = [r for r in rows if int(r[4]) > 5]
    if suspicious:
        print(f"  Orders with qty > 5:\n")
        for r in suspicious:
            items = conn.execute(text("""
                SELECT sku, product_name, size, color, quantity, unit_price
                FROM order_items WHERE so_number = :so
                ORDER BY sku
            """), {'so': r[0]}).fetchall()
            print(f"  {r[0]}  (qty={int(r[4])}, total={r[3]:,.0f})")
            for i in items:
                print(f"    {i[0] or '(no sku)':<20} {i[1] or '':<35} sz={i[2] or '-':<4} qty={i[4]}  @{i[5] or 0:,.0f}")
            print()
