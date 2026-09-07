"""Generic normalization: converts a raw source record into the unified product schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

FieldMap = dict[str, tuple[str, Callable[[Any], Any]]]


class MalformedRecordError(Exception):
    """Raised when a single raw record can't be normalized (missing/bad field)."""

    def __init__(self, source: str, raw_record: dict, field: str, original_error: Exception) -> None:
        self.source = source
        self.raw_record = raw_record
        self.field = field
        self.original_error = original_error
        super().__init__(
            f"[{source}] failed to normalize field '{field}' from {raw_record!r}: {original_error}"
        )


def normalize(raw_record: dict, source: str, field_map: FieldMap) -> dict:
    """Map a raw record's fields into the unified schema using `field_map`."""
    result: dict[str, Any] = {"source": source}
    for target_field, (source_key, caster) in field_map.items():
        try:
            result[target_field] = caster(raw_record[source_key])
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedRecordError(
                source, raw_record, target_field, exc) from exc
    return result
