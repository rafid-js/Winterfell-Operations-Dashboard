"""Cron entry point — WooCommerce product images sync (every 6 hours).
Orders and customers come from Nuport only.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.wc_sync import sync_products
from orchestrator.runner import run_task


def main():
    run_task('wc_sync', 'WooCommerce → SKU images', sync_products)


if __name__ == '__main__':
    main()
