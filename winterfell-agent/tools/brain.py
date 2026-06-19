"""
Winterfell Brain — PostgreSQL persistence for the Winterfell Agent.
All queries are parameterized — never interpolate user/agent content into SQL.
"""
import json
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
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
  sizes           TEXT[] DEFAULT ARRAY['S','M','L','XL'],
  status          TEXT DEFAULT 'draft',
  created_at      TIMESTAMP DEFAULT NOW(),
  published_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_memory (
  id           SERIAL PRIMARY KEY,
  memory_type  TEXT,
  context      TEXT,
  learning     TEXT,
  confidence   FLOAT DEFAULT 0.5,
  created_at   TIMESTAMP DEFAULT NOW(),
  updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_outcomes (
  id              SERIAL PRIMARY KEY,
  woo_id          INTEGER,
  sell_through_7d FLOAT,
  return_rate     FLOAT,
  verdict         TEXT,
  recorded_at     TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_actions (
  id           SERIAL PRIMARY KEY,
  agent        TEXT,
  action_type  TEXT,
  payload      JSONB,
  status       TEXT DEFAULT 'pending',
  created_at   TIMESTAMP DEFAULT NOW(),
  resolved_at  TIMESTAMP
);
"""


@contextmanager
def get_connection():
    conn = psycopg2.connect(config.BRAIN_DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def ensure_tables_exist():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()


# ── Products ─────────────────────────────────────────────────────────────────

def save_product(data: dict):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO products (
                    woo_id, name, category, color_primary, color_secondary,
                    style_tags, fabric, gender_target, price
                ) VALUES (
                    %(woo_id)s, %(name)s, %(category)s, %(color_primary)s, %(color_secondary)s,
                    %(style_tags)s, %(fabric)s, %(gender_target)s, %(price)s
                )
                ON CONFLICT (woo_id) DO NOTHING
            """, {
                'woo_id':          data.get('woo_id'),
                'name':            data.get('name'),
                'category':        data.get('category'),
                'color_primary':   data.get('color_primary'),
                'color_secondary': data.get('color_secondary'),
                'style_tags':      data.get('style_tags') or [],
                'fabric':          data.get('fabric'),
                'gender_target':   data.get('gender_target'),
                'price':           data.get('price') or 0,
            })
        conn.commit()


def update_product_status(woo_id: int, status: str, price: int = None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if price is not None:
                cur.execute("""
                    UPDATE products SET status = %s, price = %s,
                        published_at = CASE WHEN %s = 'publish' THEN NOW() ELSE published_at END
                    WHERE woo_id = %s
                """, (status, price, status, woo_id))
            else:
                cur.execute("""
                    UPDATE products SET status = %s,
                        published_at = CASE WHEN %s = 'publish' THEN NOW() ELSE published_at END
                    WHERE woo_id = %s
                """, (status, status, woo_id))
        conn.commit()


def delete_product(woo_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE woo_id = %s", (woo_id,))
        conn.commit()


def get_product(woo_id: int) -> dict:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE woo_id = %s", (woo_id,))
            row = cur.fetchone()
            return dict(row) if row else None


# ── Pending actions (approval gate) ─────────────────────────────────────────

def create_pending_action(agent: str, action_type: str, payload: dict) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pending_actions (agent, action_type, payload)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (agent, action_type, json.dumps(payload)))
            action_id = cur.fetchone()[0]
        conn.commit()
        return action_id


def get_latest_pending_action(agent: str = None) -> dict:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if agent:
                cur.execute("""
                    SELECT * FROM pending_actions
                    WHERE status = 'pending' AND agent = %s
                    ORDER BY created_at DESC LIMIT 1
                """, (agent,))
            else:
                cur.execute("""
                    SELECT * FROM pending_actions
                    WHERE status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                """)
            row = cur.fetchone()
            return dict(row) if row else None


def resolve_pending_action(action_id: int, status: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pending_actions SET status = %s, resolved_at = NOW()
                WHERE id = %s
            """, (status, action_id))
        conn.commit()


# ── Outcomes (for self-learning) ────────────────────────────────────────────

def record_outcome(woo_id: int, sell_through_7d: float, return_rate: float, verdict: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO product_outcomes (woo_id, sell_through_7d, return_rate, verdict)
                VALUES (%s, %s, %s, %s)
            """, (woo_id, sell_through_7d, return_rate, verdict))
        conn.commit()
