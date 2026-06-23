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

# One row per base product, keyed on the SKU parent (split_part(sku,'-',1)).
# A base product = all size variants of one colourway; colours stay separate
# (they look different in a photo). product_name is the clean display name with
# the size suffix stripped. stock_json holds per-size live stock, e.g.
# {"M": 12, "L": 8, "30": 4}. price is whole BDT (skus.selling_price is taka).
PRODUCT_EMBEDDINGS_SQL = """
CREATE TABLE IF NOT EXISTS product_embeddings (
    representative_sku     VARCHAR(200) PRIMARY KEY,
    product_name           VARCHAR(300) NOT NULL,
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

# Re-key an already-created table: older builds used product_name as the PK,
# which collides across size/colour variants. This table is a rebuildable index,
# so dropping rows that lack the new key is safe (the indexer repopulates it).
MIGRATE_PE_PK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'product_embeddings' AND column_name = 'representative_sku'
    ) THEN
        ALTER TABLE product_embeddings ADD COLUMN representative_sku VARCHAR(200);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.key_column_usage
        WHERE constraint_name = 'product_embeddings_pkey'
          AND table_name = 'product_embeddings'
          AND column_name = 'product_name'
    ) THEN
        ALTER TABLE product_embeddings DROP CONSTRAINT product_embeddings_pkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'product_embeddings' AND constraint_type = 'PRIMARY KEY'
    ) THEN
        DELETE FROM product_embeddings WHERE representative_sku IS NULL;
        ALTER TABLE product_embeddings ALTER COLUMN representative_sku SET NOT NULL;
        ALTER TABLE product_embeddings ADD PRIMARY KEY (representative_sku);
    END IF;
END $$;
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
        print("Re-keying product_embeddings on representative_sku ...")
        conn.execute(text(MIGRATE_PE_PK_SQL))
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
