"""Cron entry point — Zoho Books → Brain sync (every 6 hours)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.zoho_sync import sync_invoices, sync_payments
from orchestrator.runner import run_task


def main():
    run_task('zoho_sync', 'Zoho Books → Brain sync', sync_invoices)
    run_task('zoho_sync', 'Zoho Books → Brain sync (payments)', sync_payments)


if __name__ == '__main__':
    main()
