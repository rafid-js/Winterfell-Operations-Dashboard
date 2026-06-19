"""
Product Agent — Winterfell's first agent.

Pipeline: photo in → Claude Vision analysis → generated content → (approve) →
WooCommerce draft + Brain save → Telegram review message → (approve) → publish/delete.

Gated actions (create_woocommerce_draft, publish_product, delete_product) are never
executed directly by the tool loop. They're staged as a pending_action and only run
once Rafid replies "yes" on Telegram or clicks Approve on the Agents dashboard page —
see confirm_pending_action() below.
"""
import os
import json

from anthropic import Anthropic
from dotenv import load_dotenv

from orchestrator import pending_actions, agent_memory, telegram_alert
from orchestrator.agent_tools import vision, content as content_tool, woocommerce, brain as agent_brain

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
AGENT_MODEL = 'claude-sonnet-4-6'
AGENT_NAME = "product_agent"

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
6. Once approved and the draft is created, it gets saved to Brain automatically
7. Tell Rafid he can reply "publish [id]", "price [id] [amount]", or "delete [id]"

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

_GATE_ALIASES = {
    "create_woocommerce_draft": "create_woocommerce_draft",
    "publish_woocommerce_product": "publish_product",
    "delete_woocommerce_product": "delete_product",
}


def execute_tool(name: str, tool_input: dict) -> dict:
    if name in _GATE_ALIASES:
        action_id = pending_actions.create(AGENT_NAME, _GATE_ALIASES[name], tool_input)
        return {"staged": True, "action_id": action_id,
                "message": "Staged — will only run once Rafid replies 'yes'."}

    if name == "analyze_product_image":
        return vision.analyze_product_image(
            tool_input["image_base64"], tool_input["media_type"], tool_input.get("user_notes", "")
        )

    if name == "generate_product_content":
        memory_block = agent_memory.memories_as_prompt_block(AGENT_NAME, memory_type="content")
        return content_tool.generate_product_content(
            tool_input["product_data"], tool_input.get("user_notes", ""), memory_block
        )

    if name == "upload_image_to_woocommerce":
        return woocommerce.upload_image(tool_input["image_base64"], tool_input.get("filename"))

    if name == "send_telegram_message":
        telegram_alert.send(tool_input["message"])
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


def run_agent(user_message: str, image_data: dict = None):
    """Drive the Claude tool-use loop until it stops calling tools."""
    memory_block = agent_memory.memories_as_prompt_block(AGENT_NAME)
    system = SYSTEM_PROMPT if not memory_block else f"{SYSTEM_PROMPT}\n\n{memory_block}"

    messages = [_build_initial_message(user_message, image_data)]

    for _ in range(10):  # safety cap on tool-call rounds
        response = _client.messages.create(
            model=AGENT_MODEL, max_tokens=4096,
            tools=TOOLS, system=system, messages=messages,
        )

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = execute_tool(block.name, block.input)
                except Exception as e:
                    result = {"error": str(e)}
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


def confirm_pending_action(action_id: int = None, correction_text: str = ""):
    """Rafid approved (possibly with a correction) — execute the staged action for real."""
    action = pending_actions.get_by_id(action_id) if action_id else pending_actions.get_latest(AGENT_NAME)
    if not action:
        telegram_alert.send("⚠️ Nothing pending to confirm.")
        return {"error": "no pending action"}

    action_type = action["action_type"]
    payload = action["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    try:
        if action_type == "create_woocommerce_draft":
            result = woocommerce.create_product(
                payload["content"], payload["media_id"],
                payload.get("category_slug", "other"), payload.get("price", "0"),
            )
            agent_brain.save_product({
                "woo_id":   result["product_id"],
                "name":     payload["content"]["product_name"],
                "category": payload.get("category_slug"),
                "price":    int(payload.get("price") or 0),
            })
            telegram_alert.send(
                f"✅ Draft created\n{payload['content']['product_name']}\n"
                f"Preview: {result['preview_url']}\n\n"
                f"Reply 'publish {result['product_id']}' or 'price {result['product_id']} 1100' to go live."
            )

        elif action_type == "publish_product":
            result = woocommerce.publish_product(payload["product_id"], payload.get("price"))
            agent_brain.update_product_status(
                payload["product_id"], "publish",
                int(payload["price"]) if payload.get("price") else None,
            )
            telegram_alert.send(f"✅ Published: {result['permalink']}")

        elif action_type == "delete_product":
            woocommerce.delete_product(payload["product_id"])
            agent_brain.delete_product(payload["product_id"])
            telegram_alert.send(f"✅ Deleted product {payload['product_id']}")

        pending_actions.resolve(action["id"], "confirmed")

    except Exception as e:
        pending_actions.resolve(action["id"], "failed")
        telegram_alert.send(f"❌ Action failed: {e}")
        return {"error": str(e)}

    if correction_text:
        agent_memory.save_memory(
            AGENT_NAME, memory_type="content",
            context=f"{action_type}: {json.dumps(payload)[:300]}",
            learning=correction_text, confidence=0.6,
        )

    return {"ok": True}


def reject_pending_action(action_id: int = None):
    action = pending_actions.get_by_id(action_id) if action_id else pending_actions.get_latest(AGENT_NAME)
    if not action:
        return {"error": "no pending action"}
    pending_actions.resolve(action["id"], "rejected")
    telegram_alert.send("❌ Cancelled.")
    return {"ok": True}
