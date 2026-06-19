"""
Shared memory system — used by every agent (product, ads, inventory, finance, orders).
Stores durable "learnings" (agent_memory) and product performance outcomes
(product_outcomes) so future runs can adapt instead of repeating mistakes.
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain'))
from db import get_connection  # noqa: E402


def get_memories(agent: str = 'product_agent', memory_type: str = None, limit: int = 10) -> list:
    """Return recent learnings for an agent, highest-confidence/newest first."""
    with get_connection() as conn:
        if memory_type:
            rows = conn.execute(text("""
                SELECT memory_type, context, learning, confidence, updated_at
                FROM agent_memory
                WHERE agent = :agent AND memory_type = :memory_type
                ORDER BY confidence DESC, updated_at DESC
                LIMIT :limit
            """), {'agent': agent, 'memory_type': memory_type, 'limit': limit}).mappings().all()
        else:
            rows = conn.execute(text("""
                SELECT memory_type, context, learning, confidence, updated_at
                FROM agent_memory
                WHERE agent = :agent
                ORDER BY confidence DESC, updated_at DESC
                LIMIT :limit
            """), {'agent': agent, 'limit': limit}).mappings().all()
        return [dict(r) for r in rows]


def save_memory(agent: str, memory_type: str, context: str, learning: str, confidence: float = 0.5):
    """Record a new learning, e.g. from a user correction after a confirmation."""
    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO agent_memory (agent, memory_type, context, learning, confidence)
            VALUES (:agent, :memory_type, :context, :learning, :confidence)
        """), {
            'agent': agent, 'memory_type': memory_type,
            'context': context, 'learning': learning, 'confidence': confidence,
        })
        conn.commit()


def memories_as_prompt_block(agent: str = 'product_agent', memory_type: str = None, limit: int = 10) -> str:
    """Render recent memories as a block to inject into an agent's system prompt."""
    memories = get_memories(agent=agent, memory_type=memory_type, limit=limit)
    if not memories:
        return ""
    lines = [f"- {m['learning']}" for m in memories]
    return "Things you've learned from past corrections:\n" + "\n".join(lines)


def record_outcome(woo_id: int, sell_through_7d: float, return_rate: float, verdict: str):
    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO product_outcomes (woo_id, sell_through_7d, return_rate, verdict)
            VALUES (:woo_id, :sell_through_7d, :return_rate, :verdict)
        """), {
            'woo_id': woo_id, 'sell_through_7d': sell_through_7d,
            'return_rate': return_rate, 'verdict': verdict,
        })
        conn.commit()
