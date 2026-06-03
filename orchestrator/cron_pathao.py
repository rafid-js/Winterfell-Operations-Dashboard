"""Cron entry point — Pathao waybill sync (daily 8AM)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.pathao_sync import sync_waybills
from orchestrator.runner import run_task


def main():
    run_task('pathao_sync', 'Pathao waybill sync', sync_waybills)


if __name__ == '__main__':
    main()
