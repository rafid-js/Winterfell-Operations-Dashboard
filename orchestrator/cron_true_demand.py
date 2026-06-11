"""Cron entry point — True Demand calculator (daily 07:00)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task


def main():
    from orchestrator.inventory import true_demand
    run_task('true_demand', 'True demand calculator', true_demand.run)


if __name__ == '__main__':
    main()
