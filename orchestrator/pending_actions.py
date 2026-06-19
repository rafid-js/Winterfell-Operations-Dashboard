"""
Approval gate — shared by every agent. Actions that should never auto-run
(create_woocommerce_draft, publish_product, delete_product, ...) are staged
here instead of executed directly. They only run once approved via Telegram
reply ("yes") or the Operations dashboard's Agents page.
"""
import os
import sys
import json

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
from db import get_connection  # noqa: E402


def create(agent: str, action_type: str, payload: dict) -> int:
    with get_connection() as conn:
        row = conn.execute(text("""
            INSERT INTO pending_actions (agent, action_type, payload)
            VALUES (:agent, :action_type, :payload)
            RETURNING id
        """), {'agent': agent, 'action_type': action_type, 'payload': json.dumps(payload)}).fetchone()
        conn.commit()
        return row[0]


def get_latest(agent: str = None) -> dict:
    with get_connection() as conn:
        if agent:
            row = conn.execute(text("""
                SELECT * FROM pending_actions
                WHERE status = 'pending' AND agent = :agent
                ORDER BY created_at DESC LIMIT 1
            """), {'agent': agent}).mappings().first()
        else:
            row = conn.execute(text("""
                SELECT * FROM pending_actions
                WHERE status = 'pending'
                ORDER BY created_at DESC LIMIT 1
            """)).mappings().first()
        return dict(row) if row else None


def get_by_id(action_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(text(
            "SELECT * FROM pending_actions WHERE id = :id"
        ), {'id': action_id}).mappings().first()
        return dict(row) if row else None


def list_pending(limit: int = 20) -> list:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM pending_actions
            WHERE status = 'pending'
            ORDER BY created_at DESC LIMIT :limit
        """), {'limit': limit}).mappings().all()
        return [dict(r) for r in rows]


def resolve(action_id: int, status: str):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE pending_actions SET status = :status, resolved_at = NOW()
            WHERE id = :id
        """), {'status': status, 'id': action_id})
        conn.commit()
