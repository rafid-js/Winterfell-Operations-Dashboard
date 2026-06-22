"""
Customer Support Agent — schema migration (core).

Creates the product_embeddings (pgvector image-match index), cs_conversations
(per-customer session history) and cs_handoffs (human-handoff log) tables, plus
their indexes. The self-learning tables (cs_reply_outcomes, cs_intent_patterns,
cs_knowledge_base, cs_agent_memory) are intentionally NOT created here — they
belong to a later pass.

pgvector is already enabled in Brain (knowledge_base.embedding is vector(1536)),
so embeddings use the same 1536-dim space (OpenAI text-embedding-3-small).

Safe to re-run — uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

Run once from brain/:
  python -m sync.cs_agent_setup
"""
import os
import sys

from sqlalchemy import text

# brain/ is the parent of this file's sync/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_connection  # noqa: E402


EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

# One row per base product (grouped from skus by product_name). stock_json holds
# the per-size live stock, e.g. {"M": 12, "L": 8, "XL": 0}. price is in whole BDT
# (skus.selling_price is already taka, not paisa).
PRODUCT_EMBEDDINGS_SQL = """
CREATE TABLE IF NOT EXISTS product_embeddings (
    product_name           VARCHAR(300) PRIMARY KEY,
    representative_sku     VARCHAR(200),
    woo_product_id         INTEGER,
    category               VARCHAR(100),
    image_url              TEXT,
    description_text       TEXT,
    description_embedding  vector(1536),
    stock_json             JSONB DEFAULT '{}',
    price                  INTEGER,
    is_active              BOOLEAN DEFAULT TRUE,
    updated_at             TIMESTAMP DEFAULT NOW()
);
"""

CS_CONVERSATIONS_SQL = """
CREATE TABLE IF NOT EXISTS cs_conversations (
    id                    SERIAL PRIMARY KEY,
    channel               VARCHAR(20) NOT NULL,
    customer_id           VARCHAR(100) NOT NULL,
    customer_name         VARCHAR(200),
    messages              JSONB DEFAULT '[]',
    status                VARCHAR(20) DEFAULT 'active',
    handed_off_to         VARCHAR(100),
    pending_confirmation  JSONB,
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW()
);
"""

CS_HANDOFFS_SQL = """
CREATE TABLE IF NOT EXISTS cs_handoffs (
    id               SERIAL PRIMARY KEY,
    conversation_id  INTEGER REFERENCES cs_conversations(id),
    reason           TEXT,
    assigned_to      VARCHAR(100),
    last_message     TEXT,
    resolved_at      TIMESTAMP,
    created_at       TIMESTAMP DEFAULT NOW()
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_pe_active ON product_embeddings(is_active);",
    # ivfflat cosine index for the image-match similarity search.
    "CREATE INDEX IF NOT EXISTS idx_pe_embedding ON product_embeddings "
    "USING ivfflat (description_embedding vector_cosine_ops);",
    "CREATE INDEX IF NOT EXISTS idx_cs_conv_cust ON cs_conversations(customer_id, channel);",
    "CREATE INDEX IF NOT EXISTS idx_cs_conv_status ON cs_conversations(status);",
    "CREATE INDEX IF NOT EXISTS idx_cs_handoff_conv ON cs_handoffs(conversation_id);",
]


def run():
    with get_connection() as conn:
        pgvector_ok = True
        try:
            print("Ensuring pgvector extension ...")
            conn.execute(text(EXTENSION_SQL))
        except Exception as e:  # noqa: BLE001
            pgvector_ok = False
            print(f"  ⚠ Could not create vector extension: {e}")

        print("Creating product_embeddings ...")
        conn.execute(text(PRODUCT_EMBEDDINGS_SQL))
        print("Creating cs_conversations ...")
        conn.execute(text(CS_CONVERSATIONS_SQL))
        print("Creating cs_handoffs ...")
        conn.execute(text(CS_HANDOFFS_SQL))

        print("Creating indexes ...")
        for stmt in INDEXES_SQL:
            if "ivfflat" in stmt and not pgvector_ok:
                print("  → skipping embedding index (pgvector unavailable)")
                continue
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ Index skipped: {e}")

        conn.commit()
    print("CS agent migration complete.")


if __name__ == '__main__':
    run()
