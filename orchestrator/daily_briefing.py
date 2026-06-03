"""
Daily Telegram briefing — sent every morning at 9AM.
Summarises yesterday's performance across orders, revenue, inventory, and ads.
"""
import os
import sys
from datetime import datetime, date, timedelta
from sqlalchemy import text

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain')
sys.path.insert(0, BRAIN)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection
from orchestrator.telegram_alert import send as telegram_send


def build_briefing() -> str:
    yesterday = date.today() - timedelta(days=1)
    y = yesterday.strftime('%Y-%m-%d')

    with get_connection() as conn:

        # ── Orders ──────────────────────────────────────────────────
        orders = conn.execute(text("""
            SELECT
                COUNT(*) AS total,
                SUM(product_total) AS gross,
                SUM(payout_amount) AS collected,
                COUNT(*) FILTER (WHERE nuport_status = 'Flagged_Returned') AS returns,
                COUNT(*) FILTER (WHERE source_channel = 'WooCommerce') AS wc,
                COUNT(*) FILTER (WHERE source_channel NOT IN ('WooCommerce') OR source_channel IS NULL) AS other
            FROM orders
            WHERE DATE(order_date) = :d
        """), {'d': y}).mappings().one()

        # ── Top SKUs yesterday ────────────────────────────────────
        top_skus = conn.execute(text("""
            SELECT oi.sku, s.product_name, SUM(oi.quantity) AS qty
            FROM order_items oi
            JOIN orders o ON oi.so_number = o.so_number
            LEFT JOIN skus s ON oi.sku = s.sku
            WHERE DATE(o.order_date) = :d
            GROUP BY oi.sku, s.product_name
            ORDER BY qty DESC
            LIMIT 5
        """), {'d': y}).mappings().all()

        # ── Low stock alerts ──────────────────────────────────────
        low_stock = conn.execute(text("""
            SELECT sku, product_name, current_stock, reorder_level
            FROM skus
            WHERE is_active = TRUE AND current_stock <= reorder_level
            ORDER BY current_stock ASC
            LIMIT 5
        """)).mappings().all()

        # ── Pathao anomalies ──────────────────────────────────────
        anomalies = conn.execute(text("""
            SELECT COUNT(*) AS cnt FROM pathao_waybills
            WHERE anomaly_flag = TRUE AND updated_at >= NOW() - INTERVAL '24 hours'
        """)).scalar()

        # ── Ad spend yesterday ────────────────────────────────────
        ads = conn.execute(text("""
            SELECT
                SUM(spend_bdt) AS spend,
                SUM(impressions) AS impr,
                SUM(clicks) AS clicks
            FROM ad_spend
            WHERE date = :d
        """), {'d': y}).mappings().one()

        # ── 7-day trend ───────────────────────────────────────────
        trend = conn.execute(text("""
            SELECT DATE(order_date) AS d, COUNT(*) AS n
            FROM orders
            WHERE order_date >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(order_date)
            ORDER BY d
        """)).mappings().all()

    # ── Format ───────────────────────────────────────────────────────────────
    o = orders
    total  = o['total']  or 0
    gross  = o['gross']  or 0
    colltd = o['collected'] or 0
    rets   = o['returns'] or 0
    ret_pct = round(rets / total * 100, 1) if total else 0

    lines = [
        f"<b>☀️ Winterfell Daily Briefing — {yesterday.strftime('%d %b %Y')}</b>",
        "",
        f"<b>📦 Orders</b>",
        f"  Total: <b>{total}</b>  |  Returns: {rets} ({ret_pct}%)",
        f"  Gross: ৳{gross:,.0f}  |  Collected: ৳{colltd:,.0f}",
        f"  WooCommerce: {o['wc'] or 0}  |  Other: {o['other'] or 0}",
    ]

    if top_skus:
        lines += ["", "<b>🏆 Top SKUs</b>"]
        for sk in top_skus:
            name = (sk['product_name'] or sk['sku'])[:35]
            lines.append(f"  {sk['qty']}× {name}")

    if low_stock:
        lines += ["", "<b>⚠️ Low Stock</b>"]
        for s in low_stock:
            lines.append(f"  {s['product_name'] or s['sku']} — {s['current_stock']} left (reorder@{s['reorder_level']})")

    if anomalies:
        lines += ["", f"<b>🚨 Pathao Anomalies: {anomalies} new</b>"]

    spend = ads['spend'] or 0
    if spend:
        cpc_val = spend / ads['clicks'] if ads['clicks'] else 0
        lines += [
            "",
            f"<b>📣 Meta Ads</b>",
            f"  Spend: ৳{spend:,.0f}  |  Clicks: {ads['clicks'] or 0}  |  CPC: ৳{cpc_val:.1f}",
        ]

    if trend:
        bar = ''.join(
            '█' if r['n'] >= (total or 1) * 0.8
            else '▓' if r['n'] >= (total or 1) * 0.5
            else '░'
            for r in trend
        )
        orders_7d = sum(r['n'] for r in trend)
        lines += ["", f"<b>📈 7-day orders:</b> {orders_7d} total  [{bar}]"]

    return '\n'.join(lines)


def run():
    print(f"\n=== Daily Briefing  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    try:
        msg = build_briefing()
        sent = telegram_send(msg)
        if sent:
            print("  ✓ Briefing sent via Telegram")
        else:
            print("  ⚠ Telegram not configured — briefing not sent")
            print(msg)
    except Exception as e:
        print(f"  ✗ Briefing failed: {e}")
        raise


if __name__ == '__main__':
    run()
