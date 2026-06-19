"""Telegram alert helper. Sends a message to TELEGRAM_CHAT_ID via TELEGRAM_BOT_TOKEN.

A second, separate bot (AGENT_TELEGRAM_BOT_TOKEN / AGENT_TELEGRAM_CHAT_ID) can be
used for the Winterfell Agent so it doesn't mix into the daily briefing / reorder
alert chat. Falls back to the main bot if the agent-specific vars aren't set.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'brain', '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')

AGENT_BOT_TOKEN = os.getenv('AGENT_TELEGRAM_BOT_TOKEN') or BOT_TOKEN
AGENT_CHAT_ID   = os.getenv('AGENT_TELEGRAM_CHAT_ID') or CHAT_ID


def send(message: str, bot_token: str = None, chat_id: str = None) -> bool:
    bot_token = bot_token or BOT_TOKEN
    chat_id = chat_id or CHAT_ID
    if not bot_token or not chat_id:
        print(f'  ⚠ Telegram send skipped — missing bot_token or chat_id '
              f'(bot_token set={bool(bot_token)}, chat_id set={bool(chat_id)})', flush=True)
        return False
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message},
            timeout=10,
        )
        if r.status_code != 200:
            print(f'  ⚠ Telegram send failed: {r.status_code} {r.text}', flush=True)
        return r.status_code == 200
    except Exception as e:
        print(f'  ⚠ Telegram alert failed: {e}', flush=True)
        return False


def download_photo(file_id: str, bot_token: str = None) -> bytes:
    """Resolve a Telegram file_id to its bytes (e.g. for an incoming product photo)."""
    bot_token = bot_token or BOT_TOKEN
    r = requests.get(
        f'https://api.telegram.org/bot{bot_token}/getFile',
        params={'file_id': file_id}, timeout=15,
    )
    r.raise_for_status()
    file_path = r.json()['result']['file_path']

    r = requests.get(
        f'https://api.telegram.org/file/bot{bot_token}/{file_path}', timeout=30,
    )
    r.raise_for_status()
    return r.content


def is_authorized_chat(chat_id, expected_chat_id: str = None) -> bool:
    expected_chat_id = expected_chat_id or CHAT_ID
    return expected_chat_id is not None and str(chat_id) == str(expected_chat_id)
