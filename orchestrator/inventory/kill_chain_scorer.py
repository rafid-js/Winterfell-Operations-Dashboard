"""
kill_chain_scorer.py — dead-stock scoring + exit strategy.

Pure scoring (score_group) is called inline by reorder_engine for
healthy-but-underselling products. log_dead_stock writes a dead_stock_log row
for anything that reaches Markdown or worse. An optional Claude call produces a
one-line ops exit instruction when ANTHROPIC_API_KEY is set.
"""
import json
import math
import os

from sqlalchemy import text


# ── scoring ───────────────────────────────────────────────────────────────────
def score_group(row):
    """Compute kill-chain score + stage from a reorder_queue row dict.
    Returns a partial dict to merge back into the row. Pure (no DB)."""
    days_no_sale = row.get('days_since_last_sale')
    days_no_sale = days_no_sale if days_no_sale is not None else 45
    sell_through = float(row.get('sell_through_pct') or 0)
    stock_age = row.get('stock_age_days') or 0
    return_rate = float(row.get('return_rate_pct') or 0)

    score = 0.0
    score += min(days_no_sale / 45.0, 1.0) * 3       # recency      max 3
    score += (1 - sell_through / 100.0) * 3          # sell-through max 3
    score += min(stock_age / 90.0, 1.0) * 2          # age          max 2
    score += min(return_rate / 100.0, 1.0) * 2       # returns      max 2
    score = round(min(score, 10.0), 2)

    # Three-strike rule overrides stage on prolonged no-sale.
    strike = 0
    if days_no_sale >= 21:
        strike = 1
    if days_no_sale >= 35:
        strike = 2
    if days_no_sale >= 45:
        strike = 3

    if strike >= 3:
        stage = 'Dead'
    elif score < 3:
        stage = None
    elif score < 5:
        stage = 'Watch'
    elif score < 7:
        stage = 'Markdown'
    elif score < 8.5:
        stage = 'Bundle'
    elif score < 9.5:
        stage = 'Liquidate'
    else:
        stage = 'Dead'

    # Strike-driven minimum stage (Markdown at 35d, Liquidate at 45d).
    if strike == 2 and stage in (None, 'Watch'):
        stage = 'Markdown'

    return {'kill_chain_score': score, 'kill_chain_stage': stage,
            'strike_count': strike}


def _suggested_discount(row):
    stock = int(row.get('stock_total') or 0)
    current_velocity = float(row.get('daily_velocity') or 0)
    if stock <= 0:
        return 0
    required_velocity = stock / 14.0          # clear in 14 days
    mult = required_velocity / max(current_velocity, 0.1)
    return int(min(math.ceil((mult - 1) * 15), 50))


def _suggested_action(stage):
    return {
        'Markdown': 'Markdown', 'Bundle': 'Bundle',
        'Liquidate': 'Wholesale', 'Dead': 'Write-off',
    }.get(stage, 'Watch')


# ── persistence ───────────────────────────────────────────────────────────────
def log_dead_stock(conn, row):
    """UPSERT a dead_stock_log entry for a suppressed product (one row per sku_base
    while Active). Adds a Claude exit recommendation when available."""
    stage = row.get('kill_chain_stage')
    discount = _suggested_discount(row)
    action = _suggested_action(stage)
    bundle_with = _bundle_pairing(conn, row.get('category'))
    rec = _claude_recommendation(row, stage, discount) if os.environ.get('ANTHROPIC_API_KEY') else None
    brand_risk = (rec or {}).get('brand_risk') if isinstance(rec, dict) else None
    rec_text = json.dumps(rec) if isinstance(rec, dict) else rec

    existing = conn.execute(text(
        "SELECT id FROM dead_stock_log WHERE sku_base = :b AND status <> 'Cleared' LIMIT 1"
    ), {'b': row['sku_base']}).fetchone()

    params = {
        'b': row['sku_base'],
        'pn': row.get('product_name'),
        'units': int(row.get('stock_total') or 0),
        'cap': row.get('capital_at_risk_bdt'),
        'stage': stage,
        'score': row.get('kill_chain_score'),
        'dnls': row.get('days_since_last_sale'),
        'stp': row.get('sell_through_pct'),
        'action': action,
        'disc': discount,
        'bundle': bundle_with,
        'rec': rec_text,
        'risk': brand_risk,
        'strike': row.get('strike_count', 0),
    }
    if existing:
        conn.execute(text("""
            UPDATE dead_stock_log SET
              units_stuck=:units, capital_locked_bdt=:cap, kill_chain_stage=:stage,
              kill_chain_score=:score, days_since_last_sale=:dnls, sell_through_pct=:stp,
              suggested_action=:action, suggested_discount_pct=:disc, bundle_with_sku=:bundle,
              claude_recommendation=COALESCE(:rec, claude_recommendation),
              brand_risk_rating=COALESCE(:risk, brand_risk_rating), strike_count=:strike
            WHERE id=:id
        """), {**params, 'id': existing._mapping['id']})
    else:
        conn.execute(text("""
            INSERT INTO dead_stock_log (
              sku_base, product_name, units_stuck, capital_locked_bdt, kill_chain_stage,
              kill_chain_score, days_since_last_sale, sell_through_pct, suggested_action,
              suggested_discount_pct, bundle_with_sku, claude_recommendation,
              brand_risk_rating, status, strike_count
            ) VALUES (
              :b, :pn, :units, :cap, :stage, :score, :dnls, :stp, :action,
              :disc, :bundle, :rec, :risk, 'Active', :strike
            )
        """), params)


def _bundle_pairing(conn, category):
    """Top-selling SKU in the same category to pair a slow mover with."""
    if not category:
        return None
    row = conn.execute(text("""
        SELECT product_name FROM skus
        WHERE category = :c AND is_active = TRUE
        ORDER BY COALESCE(sell_through_rate, 0) DESC NULLS LAST,
                 COALESCE(total_sold, 0) DESC
        LIMIT 1
    """), {'c': category}).fetchone()
    return row._mapping['product_name'] if row else None


def _claude_recommendation(row, stage, discount):
    """Best-effort one-line exit strategy via Claude. Returns dict or None."""
    try:
        import requests
        prompt = (
            "You are Winterfell's inventory exit strategist. Brand: Gen Z fast "
            "fashion, Bangladesh, premium positioning.\n"
            f"SKU: {row.get('product_name')} | Stage: {stage} | "
            f"Score: {row.get('kill_chain_score')}\n"
            f"Stock: {row.get('stock_total')} pcs | Capital locked: BDT {row.get('capital_at_risk_bdt')}\n"
            f"Days no sale: {row.get('days_since_last_sale')} | "
            f"Sell-through: {row.get('sell_through_pct')}% | Return rate: {row.get('return_rate_pct')}%\n"
            f"Suggested discount: {discount}%\n"
            "Recommend exit action, timeline, discount depth, bundle option, brand "
            "risk (Low/Medium/High), and a one-line instruction for the ops team. "
            'Respond ONLY in JSON: {"action","timeline","discount_pct","bundle_sku",'
            '"brand_risk","ops_instruction"}'
        )
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': os.environ['ANTHROPIC_API_KEY'],
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 400,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        text_out = resp.json()['content'][0]['text'].strip()
        if text_out.startswith('```'):
            text_out = text_out.strip('`').split('\n', 1)[-1].rsplit('```', 1)[0]
        return json.loads(text_out)
    except Exception:
        return None
