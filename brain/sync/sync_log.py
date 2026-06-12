"""
Sync log helpers — track last successful sync per source/type.
Every sync script calls start() at the beginning and finish() at the end.
The next run calls last_record_at() to get the incremental filter date.

Usage:
    from sync.sync_log import SyncLog

    log = SyncLog('nuport', 'inventory')
    since = log.last_record_at()          # None on first run → full sync
    try:
        records = fetch_data(since=since)
        # ... process records ...
        log.finish(records_synced=len(records), last_record_at=newest_date)
    except Exception as e:
        log.error(str(e))
        raise
"""
import sys
from datetime import datetime
from typing import Optional
from sqlalchemy import text

sys.path.insert(0, __file__.rsplit('/sync', 1)[0])
from db import get_connection


class SyncLog:
    def __init__(self, source: str, sync_type: str):
        self.source    = source
        self.sync_type = sync_type
        self._id       = None
        self._start()

    def _start(self):
        with get_connection() as conn:
            row = conn.execute(text("""
                INSERT INTO sync_log (source, sync_type, started_at, status)
                VALUES (:source, :sync_type, NOW(), 'running')
                RETURNING id
            """), {'source': self.source, 'sync_type': self.sync_type}).fetchone()
            conn.commit()
            self._id = row[0]

    def last_record_at(self) -> Optional[datetime]:
        """Return the last_record_at from the most recent successful sync."""
        with get_connection() as conn:
            row = conn.execute(text("""
                SELECT last_record_at FROM sync_log
                WHERE source = :source
                  AND sync_type = :sync_type
                  AND status = 'success'
                ORDER BY finished_at DESC
                LIMIT 1
            """), {'source': self.source, 'sync_type': self.sync_type}).fetchone()
            return row[0] if row else None

    def finish(self, records_synced: int = 0, last_record_at: datetime = None):
        with get_connection() as conn:
            conn.execute(text("""
                UPDATE sync_log
                SET status         = 'success',
                    finished_at    = NOW(),
                    records_synced = :records,
                    last_record_at = :last_record_at
                WHERE id = :id
            """), {
                'records':        records_synced,
                'last_record_at': last_record_at,
                'id':             self._id,
            })
            conn.commit()

    def error(self, msg: str):
        with get_connection() as conn:
            conn.execute(text("""
                UPDATE sync_log
                SET status    = 'error',
                    finished_at = NOW(),
                    error_msg = :msg
                WHERE id = :id
            """), {'msg': msg[:500], 'id': self._id})
            conn.commit()
