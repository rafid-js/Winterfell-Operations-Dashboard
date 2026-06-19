"""Telegram alert helper. Sends a message to TELEGRAM_CHAT_ID via TELEGRAM_BOT_TOKEN."""
import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'brain', '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')


def send(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f'  ⚠ Telegram alert failed: {e}')
        return False


def download_photo(file_id: str) -> bytes:
    """Resolve a Telegram file_id to its bytes (e.g. for an incoming product photo)."""
    r = requests.get(
        f'https://api.telegram.org/bot{BOT_TOKEN}/getFile',
        params={'file_id': file_id}, timeout=15,
    )
    r.raise_for_status()
    file_path = r.json()['result']['file_path']

    r = requests.get(
        f'https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}', timeout=30,
    )
    r.raise_for_status()
    return r.content


def is_authorized_chat(chat_id) -> bool:
    return CHAT_ID is not None and str(chat_id) == str(CHAT_ID)
