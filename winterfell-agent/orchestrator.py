"""
Orchestrator — routes incoming Telegram events to the right agent.

Today only product_agent is built, so every photo and command goes there.
As ads_agent / inventory_agent / finance_agent / orders_agent get built out,
add routing rules here based on intent instead of hardcoding product_agent.
"""
import base64

from agents import product_agent
from tools import telegram

_CONFIRM_WORDS = {"yes", "y", "confirm", "approve"}
_REJECT_WORDS = {"no", "n", "cancel", "reject"}


async def handle_photo(image_bytes: bytes, media_type: str, caption: str = ""):
    image_data = {
        "base64": base64.b64encode(image_bytes).decode(),
        "media_type": media_type,
    }
    await product_agent.run_agent(caption, image_data)


async def handle_text(text: str):
    stripped = text.strip()
    first_word = stripped.split(" ", 1)[0].lower() if stripped else ""

    if first_word in _CONFIRM_WORDS:
        correction = stripped[len(first_word):].strip().lstrip(",").strip()
        await product_agent.confirm_pending_action(correction)
        return

    if first_word in _REJECT_WORDS:
        await product_agent.reject_pending_action()
        return

    # Everything else (publish [id], price [id] [amount], delete [id], or
    # anything unrecognized) is handled by product_agent's own command logic.
    await product_agent.run_agent(stripped)
