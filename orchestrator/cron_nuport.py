"""Cron entry point — Nuport → Brain sync (every 15 min)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.nuport_sync import sync_inventory, sync_products
from orchestrator.runner import run_task


def main():
    run_task('nuport_sync', 'Nuport → Brain sync', sync_inventory)
    run_task('nuport_sync', 'Nuport → Brain sync (products)', sync_products)


if __name__ == '__main__':
    main()
