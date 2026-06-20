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


def _agent_send(message: str) -> bool:
    """Send via the agent's own bot/chat (falls back to the main bot if unset)."""
    return telegram_alert.send(message, telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)


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
- "category [id] [slug]" → stage update_product_category, ask Rafid to confirm
- A correction with no product id (e.g. "category should be drop shoulder polo") and no
  image attached → you have no way to know which product it's about. Ask Rafid to resend
  it as "category [id] [slug]" using the id from the draft/confirmation message.
- Anything else → tell Rafid you did not understand, list what you can do

create_woocommerce_draft, publish_product, delete_product, and update_product_category are
gated — calling them only stages the action and asks Rafid to confirm. They do not execute
until he replies "yes".

Always send a Telegram message at the end so Rafid knows what happened.
If anything fails, tell Rafid exactly what went wrong in plain language."""

TOOLS = [
    {
        "name": "analyze_product_image",
        "description": "Analyze the product photo Rafid just sent using Claude Vision. Returns product type, color, fit, fabric, style tags, and suggested WooCommerce category. The image itself is already attached to this conversation — do not pass it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_notes": {"type": "string", "description": "Optional notes from user caption e.g. price, fabric"},
            },
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
        "description": "Upload the product photo already attached to this conversation to the WooCommerce media library. Returns media ID. Do not pass image data — it's looked up automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
            },
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
    {
        "name": "update_product_category",
        "description": "Stage re-categorizing an existing WooCommerce product, for Rafid's approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "category_slug": {"type": "string", "description": "e.g. drop-shoulder-polo"},
            },
            "required": ["product_id", "category_slug"],
        },
    },
]

_GATE_ALIASES = {
    "create_woocommerce_draft": "create_woocommerce_draft",
    "publish_woocommerce_product": "publish_product",
    "delete_woocommerce_product": "delete_product",
    "update_product_category": "update_category",
}


def execute_tool(name: str, tool_input: dict, image_data: dict = None) -> dict:
    print(f"[execute_tool] {name} input_keys={list(tool_input.keys())}", flush=True)
    try:
        result = _execute_tool(name, tool_input, image_data)
    except Exception as e:
        print(f"[execute_tool] {name} EXCEPTION: {e!r}", flush=True)
        raise
    print(f"[execute_tool] {name} result={json.dumps(result)[:300]}", flush=True)
    return result


def _execute_tool(name: str, tool_input: dict, image_data: dict = None) -> dict:
    if name in _GATE_ALIASES:
        action_id = pending_actions.create(AGENT_NAME, _GATE_ALIASES[name], tool_input)
        return {"staged": True, "action_id": action_id,
                "message": "Staged — will only run once Rafid replies 'yes'."}

    if name == "analyze_product_image":
        if not image_data:
            return {"error": "No image attached to this conversation."}
        return vision.analyze_product_image(
            image_data["base64"], image_data["media_type"], tool_input.get("user_notes", "")
        )

    if name == "generate_product_content":
        memory_block = agent_memory.memories_as_prompt_block(AGENT_NAME, memory_type="content")
        return content_tool.generate_product_content(
            tool_input["product_data"], tool_input.get("user_notes", ""), memory_block
        )

    if name == "upload_image_to_woocommerce":
        if not image_data:
            return {"error": "No image attached to this conversation."}
        return woocommerce.upload_image(
            image_data["base64"], tool_input.get("filename"), image_data["media_type"]
        )

    if name == "send_telegram_message":
        _agent_send(tool_input["message"])
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

    for round_num in range(10):  # safety cap on tool-call rounds
        kwargs = dict(model=AGENT_MODEL, max_tokens=4096, tools=TOOLS, system=system, messages=messages)
        if round_num == 0 and image_data:
            # Force a tool call on the photo-bearing first turn — otherwise the model
            # sometimes just announces intent ("I'll analyze this now!") as plain text
            # and ends its turn without ever calling analyze_product_image.
            kwargs["tool_choice"] = {"type": "any"}
        response = _client.messages.create(**kwargs)

        print(f"[run_agent] round={round_num} stop_reason={response.stop_reason}", flush=True)

        if response.stop_reason != "tool_use":
            # The model ended its turn with plain text instead of calling
            # send_telegram_message — send it anyway so Rafid isn't left hanging.
            closing_text = "".join(block.text for block in response.content if block.type == "text").strip()
            _agent_send(closing_text or "⚠️ Finished processing but had nothing to report — check the /agents dashboard.")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = execute_tool(block.name, block.input, image_data)
                except Exception as e:
                    result = {"error": str(e)}
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
    else:
        _agent_send("⚠️ Hit the round limit while processing — something may have gotten stuck. Check the /agents dashboard.")


def confirm_pending_action(action_id: int = None, correction_text: str = ""):
    """Rafid approved (possibly with a correction) — execute the staged action for real."""
    action = pending_actions.get_by_id(action_id) if action_id else pending_actions.get_latest(AGENT_NAME)
    if not action:
        _agent_send("⚠️ Nothing pending to confirm.")
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
            _agent_send(
                f"✅ Draft created\n{payload['content']['product_name']}\n"
                f"Edit/preview (login required): {result['preview_url']}\n\n"
                f"Reply 'publish {result['product_id']}' or 'price {result['product_id']} 1100' to go live."
            )

        elif action_type == "publish_product":
            result = woocommerce.publish_product(payload["product_id"], payload.get("price"))
            agent_brain.update_product_status(
                payload["product_id"], "publish",
                int(payload["price"]) if payload.get("price") else None,
            )
            _agent_send(f"✅ Published: {result['permalink']}")

        elif action_type == "delete_product":
            woocommerce.delete_product(payload["product_id"])
            agent_brain.delete_product(payload["product_id"])
            _agent_send(f"✅ Deleted product {payload['product_id']}")

        elif action_type == "update_category":
            result = woocommerce.update_product_category(payload["product_id"], payload["category_slug"])
            agent_brain.update_product_category(payload["product_id"], result["category"])
            _agent_send(f"✅ Category updated to {result['category']} for product {payload['product_id']}")

        pending_actions.resolve(action["id"], "confirmed")

    except Exception as e:
        pending_actions.resolve(action["id"], "failed")
        _agent_send(f"❌ Action failed: {e}")
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
    _agent_send("❌ Cancelled.")
    return {"ok": True}
