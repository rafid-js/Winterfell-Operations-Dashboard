"""
Product Agent — Winterfell's first agent.

Pipeline: photo in → Claude Vision analysis → generated content → (confirm) →
WooCommerce draft + Brain save → Telegram review message → (confirm) → publish/delete.

Gated actions (create_woocommerce_draft, publish_product, delete_product) are never
executed directly by the tool loop. They're staged as a pending_action and only run
once Rafid replies "yes" — see confirm_pending_action() below.
"""
import json

from anthropic import Anthropic

import config
from memory import memory
from tools import vision, content as content_tool, woocommerce, brain, telegram

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

AGENT_NAME = "product_agent"

GATED_TOOLS = {"create_woocommerce_draft", "publish_product", "delete_product"}

SYSTEM_PROMPT = """You are the Winterfell Agent — an AI assistant that manages product operations
for Winterfell, a Gen Z streetwear brand in Bangladesh.

Your personality: efficient, direct, no fluff. You talk to Rafid (the founder)
on Telegram. Keep messages short and clear. Use ✅ for success, ❌ for errors,
⚠️ for warnings.

When you receive a product photo:
1. First analyze the image
2. Check if it looks like a fashion/clothing product — if not, tell Rafid and stop
3. Generate all product content
4. Upload the image to WooCommerce
5. Stage the draft product for Rafid's approval (create_woocommerce_draft) — show him
   the generated content as a preview in your Telegram message and ask him to reply "yes"
6. Once approved and the draft is created, save it to Brain
7. Send Rafid a review message with the preview link, and tell him he can reply
   "publish [id]", "price [id] [amount]", or "delete [id]"

When you receive a command:
- "publish [id]" → stage publish_product, ask Rafid to confirm with "yes"
- "price [id] [amount]" → stage publish_product with that price, ask Rafid to confirm
- "delete [id]" → stage delete_product, ask Rafid to confirm
- Anything else → tell Rafid you did not understand, list what you can do

create_woocommerce_draft, publish_product, and delete_product are gated — calling them
only stages the action and asks Rafid to confirm. They do not execute until he replies "yes".

Always send a Telegram message at the end so Rafid knows what happened.
If anything fails, tell Rafid exactly what went wrong in plain language."""

TOOLS = [
    {
        "name": "analyze_product_image",
        "description": "Analyze a fashion product photo using Claude Vision. Returns product type, color, fit, fabric, style tags, and suggested WooCommerce category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_base64": {"type": "string", "description": "Base64 encoded image"},
                "media_type": {"type": "string", "description": "image/jpeg or image/png"},
                "user_notes": {"type": "string", "description": "Optional notes from user caption e.g. price, fabric"},
            },
            "required": ["image_base64", "media_type"],
        },
    },
    {
        "name": "generate_product_content",
        "description": "Generate WooCommerce product content in Bangla/English mix for Gen Z Bangladeshi audience. Returns product name, descriptions, SEO fields, tags, size guide, and WhatsApp promo line.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_data": {"type": "object", "description": "Output from analyze_product_image"},
                "user_notes": {"type": "string", "description": "Any user overrides like price or fabric"},
            },
            "required": ["product_data"],
        },
    },
    {
        "name": "upload_image_to_woocommerce",
        "description": "Upload a product image to WooCommerce media library. Returns media ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_base64": {"type": "string"},
                "filename": {"type": "string"},
            },
            "required": ["image_base64", "filename"],
        },
    },
    {
        "name": "create_woocommerce_draft",
        "description": "Stage a draft product in WooCommerce with all content for Rafid's approval. Does not go live until confirmed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "object", "description": "Output from generate_product_content"},
                "media_id": {"type": "integer", "description": "From upload_image_to_woocommerce"},
                "category_slug": {"type": "string", "description": "e.g. cargo-pants"},
                "price": {"type": "string", "description": "Optional price in BDT, default 0"},
            },
            "required": ["content", "media_id", "category_slug"],
        },
    },
    {
        "name": "save_product_to_brain",
        "description": "Save new product record to Winterfell Brain PostgreSQL database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "woo_id": {"type": "integer"},
                "name": {"type": "string"},
                "category": {"type": "string"},
                "color_primary": {"type": "string"},
                "color_secondary": {"type": "string"},
                "style_tags": {"type": "array", "items": {"type": "string"}},
                "fabric": {"type": "string"},
                "gender_target": {"type": "string"},
                "price": {"type": "integer"},
            },
            "required": ["woo_id", "name", "category"],
        },
    },
    {
        "name": "send_telegram_message",
        "description": "Send a message to Rafid on Telegram.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message text, supports markdown"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "publish_woocommerce_product",
        "description": "Stage publishing a draft WooCommerce product for Rafid's approval. Optionally set price first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "price": {"type": "string", "description": "Optional — set price before publishing"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "delete_woocommerce_product",
        "description": "Stage permanently deleting a WooCommerce product and removing it from Brain, for Rafid's approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
            },
            "required": ["product_id"],
        },
    },
]

# Map the "publish"/"delete" tool names Claude sees to the gated action types we
# actually stage and execute on confirmation.
_GATE_ALIASES = {
    "publish_woocommerce_product": "publish_product",
    "delete_woocommerce_product": "delete_product",
    "create_woocommerce_draft": "create_woocommerce_draft",
}


async def execute_tool(name: str, tool_input: dict) -> dict:
    if name in _GATE_ALIASES:
        action_type = _GATE_ALIASES[name]
        action_id = brain.create_pending_action(AGENT_NAME, action_type, tool_input)
        return {
            "staged": True,
            "action_id": action_id,
            "message": "Staged — will only run once Rafid replies 'yes'.",
        }

    if name == "analyze_product_image":
        return vision.analyze_product_image(
            tool_input["image_base64"], tool_input["media_type"], tool_input.get("user_notes", "")
        )

    if name == "generate_product_content":
        memory_block = memory.memories_as_prompt_block(memory_type="content")
        return content_tool.generate_product_content(
            tool_input["product_data"], tool_input.get("user_notes", ""), memory_block
        )

    if name == "upload_image_to_woocommerce":
        return woocommerce.upload_image(tool_input["image_base64"], tool_input.get("filename"))

    if name == "save_product_to_brain":
        brain.save_product(tool_input)
        return {"saved": True}

    if name == "send_telegram_message":
        await telegram.send_message(tool_input["message"])
        return {"sent": True}

    return {"error": f"Unknown tool: {name}"}


def _build_initial_message(user_message: str, image_data: dict = None) -> dict:
    content = []
    if image_data:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": image_data["media_type"], "data": image_data["base64"]},
        })
    content.append({"type": "text", "text": user_message or "(photo attached, no caption)"})
    return {"role": "user", "content": content}


async def run_agent(user_message: str, image_data: dict = None):
    """Drive the Claude tool-use loop until it stops calling tools."""
    memory_block = memory.memories_as_prompt_block()
    system = SYSTEM_PROMPT if not memory_block else f"{SYSTEM_PROMPT}\n\n{memory_block}"

    messages = [_build_initial_message(user_message, image_data)]

    for _ in range(10):  # safety cap on tool-call rounds
        response = _client.messages.create(
            model=config.AGENT_MODEL,
            max_tokens=4096,
            tools=TOOLS,
            system=system,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


async def confirm_pending_action(correction_text: str = ""):
    """Rafid replied 'yes' (possibly with a correction) — execute the staged action for real."""
    action = brain.get_latest_pending_action(AGENT_NAME)
    if not action:
        await telegram.send_message("⚠️ Nothing pending to confirm.")
        return

    action_type = action["action_type"]
    payload = action["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    try:
        if action_type == "create_woocommerce_draft":
            result = woocommerce.create_product(
                payload["content"], payload["media_id"],
                config.CATEGORY_IDS.get(payload.get("category_slug", "other")),
                payload.get("price", "0"),
            )
            brain.save_product({
                "woo_id":   result["product_id"],
                "name":     payload["content"]["product_name"],
                "category": payload.get("category_slug"),
                "price":    int(payload.get("price") or 0),
            })
            await telegram.send_message(
                f"✅ *Draft created*\n{payload['content']['product_name']}\n"
                f"Preview: {result['preview_url']}\n\n"
                f"Reply `publish {result['product_id']}` or `price {result['product_id']} 1100` to go live."
            )

        elif action_type == "publish_product":
            result = woocommerce.publish_product(payload["product_id"], payload.get("price"))
            brain.update_product_status(
                payload["product_id"], "publish",
                int(payload["price"]) if payload.get("price") else None,
            )
            await telegram.send_message(f"✅ Published: {result['permalink']}")

        elif action_type == "delete_product":
            woocommerce.delete_product(payload["product_id"])
            brain.delete_product(payload["product_id"])
            await telegram.send_message(f"✅ Deleted product {payload['product_id']}")

        brain.resolve_pending_action(action["id"], "confirmed")

    except Exception as e:
        brain.resolve_pending_action(action["id"], "failed")
        await telegram.send_message(f"❌ Action failed: {e}")
        return

    if correction_text:
        memory.save_memory(
            memory_type="content",
            context=f"{action_type}: {json.dumps(payload)[:300]}",
            learning=correction_text,
            confidence=0.6,
        )


async def reject_pending_action():
    action = brain.get_latest_pending_action(AGENT_NAME)
    if not action:
        return
    brain.resolve_pending_action(action["id"], "rejected")
    await telegram.send_message("❌ Cancelled.")
