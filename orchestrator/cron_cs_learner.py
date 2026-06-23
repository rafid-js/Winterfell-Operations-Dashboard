"""Cron entry point — CS agent self-learning pass (nightly)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.runner import run_task


def main():
    from orchestrator.cs_agent import nightly_learner
    run_task('cs_learner', 'CS conversation learner', nightly_learner.run)


if __name__ == '__main__':
    main()
