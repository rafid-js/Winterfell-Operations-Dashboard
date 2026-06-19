"""
Winterfell Agent — all environment variables in one place.
Never hardcode secrets. Set these in Railway → Variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')  # only this chat is ever served

WOOCOMMERCE_URL    = os.getenv('WOOCOMMERCE_URL', '').rstrip('/')
WOOCOMMERCE_KEY    = os.getenv('WOOCOMMERCE_KEY')
WOOCOMMERCE_SECRET = os.getenv('WOOCOMMERCE_SECRET')

BRAIN_DATABASE_URL = os.getenv('BRAIN_DATABASE_URL')

# WooCommerce category IDs
CATEGORY_IDS = {
    'cargo-pants':         os.getenv('CAT_CARGO_PANTS'),
    'drop-shoulder-tee':   os.getenv('CAT_DROP_SHOULDER_TEE'),
    'denim':               os.getenv('CAT_DENIM'),
    'knit-polo':           os.getenv('CAT_KNIT_POLO'),
    'jogger':              os.getenv('CAT_JOGGER'),
    'jacket':              os.getenv('CAT_JACKET'),
    'other':               os.getenv('CAT_OTHER'),
}

VISION_MODEL  = 'claude-sonnet-4-6'
CONTENT_MODEL = 'claude-haiku-4-5-20251001'
AGENT_MODEL   = 'claude-sonnet-4-6'

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB


def require_config():
    """Fail fast on startup if anything critical is missing."""
    missing = [name for name, val in {
        'ANTHROPIC_API_KEY':   ANTHROPIC_API_KEY,
        'TELEGRAM_BOT_TOKEN':  TELEGRAM_BOT_TOKEN,
        'TELEGRAM_CHAT_ID':    TELEGRAM_CHAT_ID,
        'WOOCOMMERCE_URL':     WOOCOMMERCE_URL,
        'WOOCOMMERCE_KEY':     WOOCOMMERCE_KEY,
        'WOOCOMMERCE_SECRET':  WOOCOMMERCE_SECRET,
        'BRAIN_DATABASE_URL':  BRAIN_DATABASE_URL,
    }.items() if not val]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
