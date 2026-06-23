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

# Letter-size guidance used in the text system prompt. The catalog also has
# numeric pant sizes (30, 32, …) which stock_line handles generically.
SIZES = ['M', 'L', 'XL', 'XXL', '3XL']

_SIZE_RANK = {s: i for i, s in enumerate(
    ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL', 'XXXL', '4XL'])}


def _size_sort_key(size):
    """Order letter sizes by the rank above, numeric sizes by value, rest last."""
    s = str(size).strip()
    if s in _SIZE_RANK:
        return (0, _SIZE_RANK[s], s)
    digits = ''.join(ch for ch in s if ch.isdigit())
    if digits:
        return (1, int(digits), s)
    return (2, 0, s)


def stock_line(stock_json):
    """Format in-stock sizes for a customer reply: 'M (12টা) · L (8টা)'.

    Shows whatever size keys exist in stock_json — letter OR numeric — so pant
    sizes (30/32/34) display the same as tee sizes. Returns None if nothing is
    in stock.
    """
    items = [(s, int(q or 0)) for s, q in (stock_json or {}).items()
             if int(q or 0) > 0]
    items.sort(key=lambda it: _size_sort_key(it[0]))
    parts = [f"{s} ({q}টা)" for s, q in items]
    return ' · '.join(parts) if parts else None
