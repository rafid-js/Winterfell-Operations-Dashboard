"""Cron entry point — WooCommerce → Brain sync (every 15 min)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.wc_sync import sync_orders, sync_products
from orchestrator.runner import run_task


def main():
    run_task('wc_sync', 'WooCommerce → Brain sync', sync_orders)
    run_task('wc_sync', 'WooCommerce → Brain sync (products)', sync_products)


if __name__ == '__main__':
    main()
