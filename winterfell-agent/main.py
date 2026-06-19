"""
Winterfell Agent — FastAPI app, Telegram webhook receiver.

Telegram delivers updates to POST /webhook/telegram. We acknowledge with 200
immediately and run the agent logic in the background so Telegram doesn't retry.
"""
import asyncio
import logging

from fastapi import FastAPI, Request

import config
import orchestrator
from tools import brain, telegram

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("winterfell-agent")

app = FastAPI(title="Winterfell Agent")


@app.on_event("startup")
def startup():
    config.require_config()
    brain.ensure_tables_exist()
    log.info("Winterfell Agent started — tables ensured.")


@app.get("/")
def health():
    return {"status": "ok", "service": "winterfell-agent"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    update = await request.json()
    message = update.get("message")
    if not message:
        return {"ok": True}

    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id != str(config.TELEGRAM_CHAT_ID):
        # Not Rafid — ignore silently, no response, no error.
        return {"ok": True}

    if "photo" in message:
        asyncio.create_task(_handle_photo_message(message))
    elif "text" in message:
        asyncio.create_task(orchestrator.handle_text(message["text"]))

    return {"ok": True}


async def _handle_photo_message(message: dict):
    try:
        photo_sizes = message["photo"]
        largest = max(photo_sizes, key=lambda p: p.get("file_size", 0) or p.get("width", 0))
        caption = message.get("caption", "")

        image_bytes = await telegram.download_photo(largest["file_id"])
        if len(image_bytes) > config.MAX_IMAGE_BYTES:
            await telegram.send_message("❌ Image too large (max 20MB). Send a smaller photo.")
            return

        await orchestrator.handle_photo(image_bytes, "image/jpeg", caption)
    except Exception as e:
        log.exception("Failed to handle photo message")
        await telegram.send_message(f"❌ Could not process photo: {e}")
