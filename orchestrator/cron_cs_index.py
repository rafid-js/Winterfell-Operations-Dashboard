"""Cron entry point — CS product embedding indexer (nightly)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task


def main():
    from orchestrator.cs_agent import indexer
    run_task('cs_index', 'CS product indexer', indexer.run)


if __name__ == '__main__':
    main()
