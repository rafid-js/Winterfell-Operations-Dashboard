"""Cron entry point — Nuport → Brain sync (every 15 min)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.nuport_sync import sync_new_orders, sync_active_orders, sync_inventory
from orchestrator.runner import run_task


def main():
    run_task('nuport_sync', 'Nuport new orders scan', sync_new_orders)
    run_task('nuport_sync', 'Nuport active order status refresh', sync_active_orders)
    run_task('nuport_sync', 'Nuport inventory (stock levels)', sync_inventory)


if __name__ == '__main__':
    main()
