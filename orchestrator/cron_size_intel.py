"""Cron entry point — Size intelligence learner (weekly Sun 06:00)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task


def main():
    from orchestrator.inventory import size_intelligence
    run_task('size_intelligence', 'Size intelligence learner', size_intelligence.run)


if __name__ == '__main__':
    main()
