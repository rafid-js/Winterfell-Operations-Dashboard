"""
Script runner — wraps any sync function with:
  - system_status tracking (RUNNING → SUCCESS / FAILED)
  - alerts_log entries
  - Telegram alert on failure

Usage in cron scripts:
    from runner import run_task
    run_task('nuport_sync', 'Nuport → Brain sync', sync_inventory)
"""
import os
import sys
import time
import traceback
from datetime import datetime

# Make brain importable
BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain')
sys.path.insert(0, BRAIN)

from sqlalchemy import text
from db import get_connection
from orchestrator.telegram_alert import send as telegram_send

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _set_status(script_name: str, **fields):
    with get_connection() as conn:
        sets   = ', '.join(f'{k} = :{k}' for k in fields)
        params = {'script_name': script_name, **fields}
        conn.execute(text(f"""
            UPDATE system_status SET {sets} WHERE script_name = :script_name
        """), params)
        conn.commit()


def _log(script_name: str, title: str, message: str, severity: str = 'INFO'):
    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO alerts_log (alert_type, severity, title, message, created_at)
            VALUES ('system', :sev, :title, :msg, NOW())
        """), {'sev': severity, 'title': f'{script_name}: {title}', 'msg': message[:2000]})
        conn.commit()


def run_task(script_name: str, display_name: str, fn, *args, **kwargs):
    """
    Run fn(*args, **kwargs) with full status tracking.
    Returns whatever fn returns.
    Raises the exception after logging if fn fails.
    """
    start = time.time()
    now   = datetime.now()

    print(f"\n{'='*60}")
    print(f'  {display_name}')
    print(f'  {now:%Y-%m-%d %H:%M:%S}')
    print(f"{'='*60}\n")

    with get_connection() as conn:
        conn.execute(text("""
            UPDATE system_status
            SET last_run_at = :ts, last_run_status = 'RUNNING', run_count = run_count + 1
            WHERE script_name = :s
        """), {'ts': now, 's': script_name})
        conn.commit()

    _log(script_name, 'Started', f'Started at {now:%H:%M:%S}')

    try:
        result = fn(*args, **kwargs)
        duration = round(time.time() - start, 1)

        _set_status(script_name,
                    last_run_status='SUCCESS',
                    last_run_duration_sec=int(duration),
                    last_error=None)
        _log(script_name, 'Success', f'Completed in {duration}s', 'SUCCESS')
        print(f'\n✓ {display_name} completed in {duration}s\n')
        return result

    except Exception as exc:
        duration  = round(time.time() - start, 1)
        tb        = traceback.format_exc()
        first_line = str(exc)[:300]

        with get_connection() as conn:
            conn.execute(text(
                "UPDATE system_status SET fail_count = fail_count + 1 WHERE script_name = :s"
            ), {'s': script_name})
            conn.commit()

        _set_status(script_name,
                    last_run_status='FAILED',
                    last_run_duration_sec=int(duration),
                    last_error=first_line)
        _log(script_name, 'FAILED', tb[:2000], 'ERROR')

        telegram_send(
            f'⚠️ <b>Winterfell: {display_name} FAILED</b>\n'
            f'Error: {first_line}\n'
            f'Time: {now:%Y-%m-%d %H:%M}'
        )

        print(f'\n✗ {display_name} FAILED after {duration}s\n{first_line}\n')
        raise
