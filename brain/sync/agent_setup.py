"""
Winterfell Agent module — schema migration.

Creates the agent_products, agent_memory, product_outcomes and pending_actions
tables used by orchestrator/agents/*. agent_products is intentionally separate
from skus — it tracks the agent's draft → publish pipeline before a product
has real SKU/variation rows, which wc_sync.py populates once it's live.

Safe to re-run — uses IF NOT EXISTS.

Run once from brain/:
  python -m sync.agent_setup
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection  # noqa: E402


AGENT_PRODUCTS_SQL = """
CREATE TABLE IF NOT EXISTS agent_products (
    id              SERIAL PRIMARY KEY,
    woo_id          INTEGER UNIQUE,
    name            TEXT NOT NULL,
    category        TEXT,
    color_primary   TEXT,
    color_secondary TEXT,
    style_tags      TEXT[],
    fabric          TEXT,
    gender_target   TEXT,
    price           INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'draft',
    created_at      TIMESTAMP DEFAULT NOW(),
    published_at    TIMESTAMP
)"""

AGENT_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id           SERIAL PRIMARY KEY,
    agent        TEXT NOT NULL DEFAULT 'product_agent',
    memory_type  TEXT,
    context      TEXT,
    learning     TEXT,
    confidence   FLOAT DEFAULT 0.5,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
)"""

PRODUCT_OUTCOMES_SQL = """
CREATE TABLE IF NOT EXISTS product_outcomes (
    id              SERIAL PRIMARY KEY,
    woo_id          INTEGER,
    sell_through_7d FLOAT,
    return_rate     FLOAT,
    verdict         TEXT,
    recorded_at     TIMESTAMP DEFAULT NOW()
)"""

PENDING_ACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id           SERIAL PRIMARY KEY,
    agent        TEXT NOT NULL DEFAULT 'product_agent',
    action_type  TEXT NOT NULL,
    payload      JSONB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMP DEFAULT NOW(),
    resolved_at  TIMESTAMP
)"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(agent, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_memory_type ON agent_memory(agent, memory_type)",
]


def run():
    with get_connection() as conn:
        print("Creating agent_products ...")
        conn.execute(text(AGENT_PRODUCTS_SQL))
        print("Creating agent_memory ...")
        conn.execute(text(AGENT_MEMORY_SQL))
        print("Creating product_outcomes ...")
        conn.execute(text(PRODUCT_OUTCOMES_SQL))
        print("Creating pending_actions ...")
        conn.execute(text(PENDING_ACTIONS_SQL))
        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            conn.execute(text(stmt))
        conn.commit()
    print("Agent migration complete.")


if __name__ == '__main__':
    run()
