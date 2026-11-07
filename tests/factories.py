"""Builders for test configurations. Customer-facing strings are Spanish on purpose."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pedidos_bot.channels.base import InboundMessage
from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.config.schema import RestaurantConfig

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "restaurants"

_BASE: dict[str, Any] = {
    "restaurant": {
        "id": "test_resto",
        "name": "Test Resto",
        "notify": {"chat_id_env": "TEST_RESTO_STAFF_CHAT_ID"},
    },
    "fulfillment": {
        "delivery": {"enabled": True, "fee": 5000, "eta_minutes": {"min": 30, "max": 45}},
        "pickup": {"enabled": True, "eta_minutes": {"min": 15, "max": 20}},
    },
    "payment_methods": [
        {"id": "cash", "label": "Efectivo", "aliases": ["efectivo"], "ask_change_for": True},
        {"id": "transfer", "label": "Transferencia", "aliases": ["nequi", "transferencia"]},
    ],
    "tone": {"greeting": "¡Hola! ¿Qué te provoca?"},
    "modifier_groups": [
        {
            "id": "remove",
            "label": "Quitar",
            "type": "remove",
            "options": [
                {"id": "no_onion", "label": "sin cebolla", "aliases": ["sin cebolla"]},
                {"id": "no_tomato", "label": "sin tomate", "aliases": ["sin tomate"]},
            ],
        },
        {
            "id": "extras",
            "label": "Adiciones",
            "type": "add",
            "max_select": 2,
            "options": [
                {"id": "bacon", "label": "tocineta", "aliases": ["tocineta"], "price": 4000},
                {"id": "cheese", "label": "queso extra", "aliases": ["queso"], "price": 3000},
                {"id": "egg", "label": "huevo", "aliases": ["huevo"], "price": 2000},
            ],
        },
        {
            "id": "size",
            "label": "Tamaño",
            "type": "choose_one",
            "required": True,
            "options": [
                {"id": "small", "label": "personal", "aliases": ["personal"]},
                {"id": "large", "label": "grande", "aliases": ["grande"], "price": 3000},
            ],
        },
    ],
    "categories": [
        {
            "id": "mains",
            "name": "Principales",
            "products": [
                {
                    "id": "classic",
                    "name": "Clásica",
                    "price": 18000,
                    "aliases": ["clasica"],
                    "modifier_groups": ["remove", "extras"],
                }
            ],
        },
        {
            "id": "drinks",
            "name": "Bebidas",
            "products": [
                {
                    "id": "soda",
                    "name": "Gaseosa",
                    "price": 4000,
                    "aliases": ["gaseosa"],
                    "modifier_groups": ["size"],
                }
            ],
        },
    ],
    "rules": [
        {"type": "upsell", "after_categories": ["mains"], "suggest_categories": ["drinks"]},
        {"type": "max_qty_per_line", "value": 10},
    ],
}


def config_dict(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(_BASE)
    data.update(overrides)
    return data


def make_config(**overrides: Any) -> RestaurantConfig:
    return RestaurantConfig.model_validate(config_dict(**overrides))


def sample_config(restaurant_id: str) -> RestaurantConfig:
    """One of the shipped configs."""
    return load_restaurant(restaurant_id, CONFIG_DIR)


def make_inbound(**fields: Any) -> InboundMessage:
    base = {
        "channel": "test",
        "external_id": "1",
        "chat_ref": "chat",
        "customer_ref": "customer",
        "sent_at": datetime(2026, 11, 6, 12, tzinfo=UTC),
    }
    return InboundMessage.model_validate(base | fields)
