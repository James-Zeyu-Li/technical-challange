"""Per-source configuration: field maps used by `normalize()`.

Each entry only declares data (source key + type caster) for the target
schema fields (id, title, price, category). Adding a new source means
adding a new map here, not new branching logic in the normalizer.
"""

from __future__ import annotations

from typing import Any


def to_str(value: Any) -> str:
    return str(value)


def to_float(value: Any) -> float:
    return float(value)


def cents_to_dollars(value: Any) -> float:
    return round(float(value) / 100, 2)


FIELD_MAPS = {
    "source_a": {
        "id": ("id", to_str),
        "title": ("name", to_str),
        "price": ("price", to_float),
        "category": ("category", to_str),
    },
    "source_b": {
        "id": ("sku", to_str),
        "title": ("title", to_str),
        "price": ("amount_cents", cents_to_dollars),
        "category": ("department", to_str),
    },
    "source_c": {
        "id": ("product_id", to_str),
        "title": ("product_name", to_str),
        "price": ("price", to_float),
        "category": ("type", to_str),
    },
}
