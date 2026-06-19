"""Generate WooCommerce product content (Banglish copy, SEO, tags) with Claude."""
import os
import re
import json

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
CONTENT_MODEL = 'claude-haiku-4-5-20251001'

_SYSTEM_PROMPT = """You are the content writer for Winterfell, a Gen Z fast-fashion brand in Bangladesh.
Write in casual Bangla/English mix (Banglish) that feels natural to 18-25 year old Bangladeshis.
Never use formal Bengali. Keep it real, punchy, hype but not cringe. No excessive emoji.
Return ONLY valid JSON:
{
  "product_name": "",
  "short_description": "",
  "long_description": "",
  "seo_title": "",
  "seo_meta_description": "",
  "size_guide_note": "",
  "woo_tags": [],
  "whatsapp_promo_line": ""
}"""


def _extract_json(text: str) -> dict:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in content response: {text[:200]}")
    return json.loads(match.group(0))


def generate_product_content(product_data: dict, user_notes: str = "", memory_block: str = "") -> dict:
    system = _SYSTEM_PROMPT
    if memory_block:
        system += f"\n\n{memory_block}"

    user_text = f"Product analysis:\n{json.dumps(product_data)}"
    if user_notes:
        user_text += f"\n\nUser notes / overrides: {user_notes}"

    response = _client.messages.create(
        model=CONTENT_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    text_out = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(text_out)
