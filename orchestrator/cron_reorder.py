"""Cron entry point — Inventory reorder check (every 6 hours)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task
from orchestrator.telegram_alert import send as telegram_send
from sqlalchemy import text
from db import get_connection


def check_reorder():
    # Refresh the inventory reorder_queue (target-stock engine + kill chain)
    # before the legacy stock-level alert, so the dashboard stays current.
    try:
        from orchestrator.inventory import reorder_engine
        reorder_engine.run()
    except Exception as e:
        print(f"  ! reorder_engine refresh failed: {e}")

    with get_connection() as conn:
        low = conn.execute(text("""
            SELECT sku, product_name, current_stock, reorder_level, reorder_quantity
            FROM skus
            WHERE is_active = TRUE
              AND current_stock <= reorder_level
            ORDER BY current_stock ASC
        """)).mappings().all()

        out_of_stock = [r for r in low if r['current_stock'] == 0]
        reorder_now  = [r for r in low if 0 < r['current_stock'] <= r['reorder_level']]

    total = len(out_of_stock) + len(reorder_now)
    if total == 0:
        print("  ✓ All stock levels healthy")
        return

    lines = [f"<b>📦 Winterfell Reorder Alert — {total} SKUs need attention</b>"]

    if out_of_stock:
        lines.append(f"\n<b>🔴 Out of Stock ({len(out_of_stock)})</b>")
        for r in out_of_stock[:10]:
            lines.append(f"  • {r['product_name'] or r['sku']}")

    if reorder_now:
        lines.append(f"\n<b>🟡 Reorder Now ({len(reorder_now)})</b>")
        for r in reorder_now[:10]:
            lines.append(f"  • {r['product_name'] or r['sku']} — {r['current_stock']} left (order {r['reorder_quantity']})")

    telegram_send('\n'.join(lines))
    print(f"  ✓ Reorder alert sent: {len(out_of_stock)} OOS, {len(reorder_now)} low")


def main():
    run_task('reorder_engine', 'Inventory reorder check', check_reorder)


if __name__ == '__main__':
    main()
