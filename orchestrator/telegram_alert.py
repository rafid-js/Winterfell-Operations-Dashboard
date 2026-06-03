"""Telegram alert helper. Sends a message to TELEGRAM_CHAT_ID via TELEGRAM_BOT_TOKEN."""
import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'brain', '.env'))


def send(message: str) -> bool:
    token   = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f'  ⚠ Telegram alert failed: {e}')
        return False
