"""Cron entry point — Meta Ads → Brain sync (every 6 hours)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync.meta_sync import sync_spend
from orchestrator.runner import run_task


def main():
    run_task('meta_sync', 'Meta Ads → Brain sync', sync_spend)


if __name__ == '__main__':
    main()
