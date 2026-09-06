"""Per-source configuration: field maps used by `normalize()`.

Each entry only declares data (source key + type caster) for the target
schema fields (id, title, price, category). Adding a new source means
adding a new map here, not new branching logic in the normalizer.
"""

from __future__ import annotations

import math
from typing import Any


def to_str(value: Any) -> str:
    # Don't blindly stringify anything: a source returning null, a bool
    # (bool is a subclass of int in Python, so it would otherwise slip
    # through as "True"/"False"), or a nested structure where a scalar is
    # expected is a malformed record, not a value we should silently coerce.
    if value is None or isinstance(value, (bool, dict, list, set, tuple)):
        raise TypeError(f"expected a scalar value, got {type(value).__name__}")
    return str(value)


def to_float(value: Any) -> float:
    # Same reasoning as to_str: a bool is technically int-like (float(True)
    # == 1.0) but never a meaningful price/quantity, so reject it explicitly.
    if isinstance(value, bool):
        raise TypeError(f"expected a numeric value, got {type(value).__name__}")
    result = float(value)
    # NaN/Infinity are "valid floats" to Python but would silently corrupt
    # any downstream sum/sort/compare over price - treat as malformed
    # instead of guessing a replacement value.
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"expected a finite number, got {result}")
    return result


def to_price(value: Any) -> float:
    # Price-specific business rule (must be >= 0) lives here, not in
    # to_float, since to_float is generic numeric type-safety and shouldn't
    # know about price semantics.
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
