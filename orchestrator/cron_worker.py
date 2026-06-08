"""
Background scheduler — runs cron jobs automatically inside the Railway container.
Started by orchestrator/start.sh alongside gunicorn.

Schedules:
  nuport_sync:    every 15 min
  wc_sync:        every 6 hours
  daily_briefing: daily 09:00 Asia/Dhaka
"""
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain', '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('cron_worker')


def _run(module: str):
    try:
        import importlib
        mod = importlib.import_module(module)
        mod.main()
    except Exception as e:
        log.error('%s crashed: %s', module, e, exc_info=True)


def job_nuport():   _run('orchestrator.cron_nuport')
def job_wc():       _run('orchestrator.cron_wc')
def job_briefing(): _run('orchestrator.cron_briefing')


if __name__ == '__main__':
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    TZ = 'Asia/Dhaka'
    sched = BlockingScheduler(timezone=TZ)

    sched.add_job(job_nuport,   IntervalTrigger(minutes=15),
                  id='nuport',   max_instances=1, misfire_grace_time=300)

    sched.add_job(job_wc,       IntervalTrigger(hours=6),
                  id='wc',       max_instances=1, misfire_grace_time=3600)

    sched.add_job(job_briefing, CronTrigger(hour=9, minute=0, timezone=TZ),
                  id='briefing', max_instances=1)

    log.info('Winterfell cron worker started (timezone=%s)', TZ)
    log.info('  nuport_sync:    every 15 min')
    log.info('  wc_sync:        every 6h')
    log.info('  daily_briefing: daily 09:00')
    sched.start()
