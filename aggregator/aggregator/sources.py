"""Per-source declarative field maps used by `normalize()`."""

from __future__ import annotations

import math
from typing import Any


def to_str(value: Any) -> str:
    # bool is a subclass of int and would otherwise silently pass through.
    if value is None or isinstance(value, (bool, dict, list, set, tuple)):
        raise TypeError(f"expected a scalar value, got {type(value).__name__}")
    return str(value)


def to_float(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError(f"expected a numeric value, got {type(value).__name__}")
    result = float(value)
    # NaN/Infinity are "valid" floats to Python but would corrupt downstream math.
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"expected a finite number, got {result}")
    return result


def to_price(value: Any) -> float:
    result = to_float(value)
    if result < 0:
        raise ValueError(f"price cannot be negative: {result}")
    return result


def cents_to_dollars(value: Any) -> float:
    return to_price(round(to_float(value) / 100, 2))


FIELD_MAPS = {
    "source_a": {
        "id": ("id", to_str),
        "title": ("name", to_str),
        "price": ("price", to_price),
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
        "price": ("price", to_price),
        "category": ("type", to_str),
    },
}
