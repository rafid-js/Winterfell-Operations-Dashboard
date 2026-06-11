"""Cron entry point — Test batch evaluator (daily 08:00)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task


def main():
    from orchestrator.inventory import test_batch
    run_task('test_batch', 'Test batch evaluator', test_batch.run)


if __name__ == '__main__':
    main()
