"""Customer Support Agent — environment configuration.

Loads brain/.env (same as every other orchestrator module). The CS agent uses
its OWN Anthropic key (CS_ANTHROPIC_API_KEY) — deliberately separate from the
product agent's ANTHROPIC_API_KEY so the two workloads bill and rate-limit
independently.
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

# ── Anthropic (separate key from the product agent) ─────────────────────────
CS_ANTHROPIC_API_KEY = os.getenv('CS_ANTHROPIC_API_KEY')

# Models — chosen for a high-volume CS workload (cheap text, capable vision).
TEXT_MODEL = os.getenv('CS_TEXT_MODEL', 'claude-haiku-4-5')
VISION_MODEL = os.getenv('CS_VISION_MODEL', 'claude-sonnet-4-6')

# ── OpenAI (embeddings only — Anthropic has no embeddings endpoint) ──────────
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
EMBEDDING_MODEL = os.getenv('CS_EMBEDDING_MODEL', 'text-embedding-3-small')

# ── Channel webhooks ────────────────────────────────────────────────────────
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN')
FB_VERIFY_TOKEN = os.getenv('FB_VERIFY_TOKEN', 'winterfell_cs_2026')
FB_APP_SECRET = os.getenv('FB_APP_SECRET')          # optional X-Hub-Signature check
IG_ACCESS_TOKEN = os.getenv('IG_ACCESS_TOKEN') or FB_PAGE_ACCESS_TOKEN
GRAPH_API_VERSION = os.getenv('GRAPH_API_VERSION', 'v21.0')

WATI_API_KEY = os.getenv('WATI_API_KEY')
WATI_ENDPOINT = (os.getenv('WATI_ENDPOINT') or '').rstrip('/')

# ── Handoff (Telegram) ──────────────────────────────────────────────────────
# Reuses the agent bot if a CS-specific bot isn't configured.
CS_TELEGRAM_BOT_TOKEN = (
    os.getenv('CS_TELEGRAM_BOT_TOKEN')
    or os.getenv('AGENT_TELEGRAM_BOT_TOKEN')
    or os.getenv('TELEGRAM_BOT_TOKEN')
)
RAFID_TELEGRAM_ID = os.getenv('RAFID_TELEGRAM_ID')
TEAM_TELEGRAM_IDS = [
    ('Tumpa', os.getenv('TUMPA_TELEGRAM_ID')),
    ('Nafiz', os.getenv('NAFIZ_TELEGRAM_ID')),
    ('Ayon', os.getenv('AYON_TELEGRAM_ID')),
]

# ── Matching ────────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = float(os.getenv('SIMILARITY_THRESHOLD', '0.82'))

# Winterfell sells M–3XL only.
SIZES = ['M', 'L', 'XL', 'XXL', '3XL']
