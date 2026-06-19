"""
Shared memory system — used by every agent (product, ads, inventory, finance, orders).
Stores durable "learnings" (agent_memory) and product performance outcomes
(product_outcomes) so future runs can adapt instead of repeating mistakes.
"""
import psycopg2.extras

from tools.brain import get_connection


def get_memories(memory_type: str = None, limit: int = 20) -> list:
    """Return recent learnings, optionally filtered by type, newest/highest-confidence first."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if memory_type:
                cur.execute("""
                    SELECT memory_type, context, learning, confidence, updated_at
                    FROM agent_memory
                    WHERE memory_type = %s
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT %s
                """, (memory_type, limit))
            else:
                cur.execute("""
                    SELECT memory_type, context, learning, confidence, updated_at
                    FROM agent_memory
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT %s
                """, (limit,))
            return [dict(row) for row in cur.fetchall()]


def save_memory(memory_type: str, context: str, learning: str, confidence: float = 0.5):
    """Record a new learning, e.g. from a user correction after a confirmation."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_memory (memory_type, context, learning, confidence)
                VALUES (%s, %s, %s, %s)
            """, (memory_type, context, learning, confidence))
        conn.commit()


def memories_as_prompt_block(memory_type: str = None, limit: int = 10) -> str:
    """Render recent memories as a block to inject into an agent's system prompt."""
    memories = get_memories(memory_type=memory_type, limit=limit)
    if not memories:
        return ""
    lines = [f"- ({m['memory_type']}) {m['learning']}" for m in memories]
    return "Things you've learned from past corrections:\n" + "\n".join(lines)


def record_outcome(woo_id: int, sell_through_7d: float, return_rate: float, verdict: str):
    from tools import brain
    brain.record_outcome(woo_id, sell_through_7d, return_rate, verdict)
