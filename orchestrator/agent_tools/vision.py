"""Analyze a product photo with Claude Vision."""
import os
import re
import json

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'brain', '.env'))

_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
VISION_MODEL = 'claude-sonnet-4-6'

_SYSTEM_PROMPT = """You are a product analyst for Winterfell, a Gen Z streetwear brand in Bangladesh.
Analyze this product photo and return ONLY valid JSON, no other text.
{
  "is_fashion_product": true/false,
  "product_type": "",
  "color_primary": "",
  "color_secondary": null,
  "fit_style": "",
  "pocket_count": null,
  "fabric_guess": "",
  "gender_target": "male/female/unisex",
  "style_mood": [],
  "suggested_category": "cargo-pants|drop-shoulder-tee|denim|knit-polo|jogger|jacket|other",
  "size_range": "S-XL"
}
If is_fashion_product is false, still return the JSON but leave other fields empty."""


def _extract_json(text: str) -> dict:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in vision response: {text[:200]}")
    return json.loads(match.group(0))


def analyze_product_image(image_base64: str, media_type: str, user_notes: str = "") -> dict:
    user_text = "Analyze this product photo."
    if user_notes:
        user_text += f"\n\nUser notes: {user_notes}"

    response = _client.messages.create(
        model=VISION_MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}},
                {"type": "text", "text": user_text},
            ],
        }],
    )
    text_out = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(text_out)
