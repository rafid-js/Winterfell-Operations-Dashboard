"""Winterfell Brain persistence for agent-created products."""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'brain'))
from db import get_connection  # noqa: E402


def save_product(data: dict):
    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO agent_products (
                woo_id, name, category, color_primary, color_secondary,
                style_tags, fabric, gender_target, price
            ) VALUES (
                :woo_id, :name, :category, :color_primary, :color_secondary,
                :style_tags, :fabric, :gender_target, :price
            )
            ON CONFLICT (woo_id) DO NOTHING
        """), {
            'woo_id':          data.get('woo_id'),
            'name':            data.get('name'),
            'category':        data.get('category'),
            'color_primary':   data.get('color_primary'),
            'color_secondary': data.get('color_secondary'),
            'style_tags':      data.get('style_tags') or [],
            'fabric':          data.get('fabric'),
            'gender_target':   data.get('gender_target'),
            'price':           data.get('price') or 0,
        })
        conn.commit()


def update_product_status(woo_id: int, status: str, price: int = None):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE agent_products SET status = :status,
                price = COALESCE(:price, price),
                published_at = CASE WHEN :status = 'publish' THEN NOW() ELSE published_at END
            WHERE woo_id = :woo_id
        """), {'status': status, 'price': price, 'woo_id': woo_id})
        conn.commit()


def update_product_category(woo_id: int, category: str):
    with get_connection() as conn:
        conn.execute(text(
            "UPDATE agent_products SET category = :category WHERE woo_id = :woo_id"
        ), {'category': category, 'woo_id': woo_id})
        conn.commit()


def delete_product(woo_id: int):
    with get_connection() as conn:
        conn.execute(text("DELETE FROM agent_products WHERE woo_id = :woo_id"), {'woo_id': woo_id})
        conn.commit()


def recent_products(limit: int = 20) -> list:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM agent_products ORDER BY created_at DESC LIMIT :limit
        """), {'limit': limit}).mappings().all()
        return [dict(r) for r in rows]
