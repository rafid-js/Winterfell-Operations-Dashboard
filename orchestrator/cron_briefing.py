"""Cron entry point — Daily Telegram briefing (daily 9AM)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.daily_briefing import run
from orchestrator.runner import run_task


def main():
    run_task('daily_briefing', 'Daily Telegram briefing', run)


if __name__ == '__main__':
    main()
