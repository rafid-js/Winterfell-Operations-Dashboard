"""Telegram Bot API calls via httpx — no bot framework needed."""
import httpx

import config

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


async def send_message(text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{_API}/sendMessage", json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=15)


async def download_photo(file_id: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_API}/getFile", params={"file_id": file_id}, timeout=15)
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]

        file_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"
        r = await client.get(file_url, timeout=30)
        r.raise_for_status()
        return r.content
