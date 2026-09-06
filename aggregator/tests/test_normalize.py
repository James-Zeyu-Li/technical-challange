import unittest

from aggregator.normalize import MalformedRecordError, normalize
from aggregator.sources import FIELD_MAPS, to_price, to_str

# "source_d" is not a real upstream in this challenge - it's a synthetic
# fixture used only to exercise normalize()'s type handling in isolation,
# so these tests don't depend on (or get confused with) any real source's
# actual fixture data.
SOURCE_D_FIELD_MAP = {
    "id": ("id", to_str),
    "title": ("title", to_str),
    "price": ("price", to_price),
    "category": ("category", to_str),
}


class TestNormalize(unittest.TestCase):
    """normalize() is one generic function; each case below is the same
    function exercised against a different source's field map."""

    def test_source_a_maps_fields_and_types(self):
        """A well-formed Source A record maps field names 1:1 with no type conversion needed."""
        raw = {"id": "a-101", "name": "Mechanical Keyboard", "price": 89.99, "category": "electronics"}
        result = normalize(raw, "source_a", FIELD_MAPS["source_a"])
        self.assertEqual(
            result,
            {
                "source": "source_a",
                "id": "a-101",
                "title": "Mechanical Keyboard",
                "price": 89.99,
                "category": "electronics",
            },
        )

    def test_source_b_maps_fields_and_converts_cents_to_dollars(self):
        """A well-formed Source B record renames fields and converts integer cents to a float dollar price."""
        raw = {"sku": "b-201", "title": "Desk Lamp", "amount_cents": 3499, "department": "home"}
        result = normalize(raw, "source_b", FIELD_MAPS["source_b"])
        self.assertEqual(
            result,
            {
                "source": "source_b",
                "id": "b-201",
                "title": "Desk Lamp",
                "price": 34.99,
                "category": "home",
            },
        )

    def test_source_b_malformed_price_raises_malformed_record_error(self):
        """The known bad fixture record (b-205) has a non-numeric amount_cents, which raises MalformedRecordError instead of silently corrupting the batch."""
        raw = {"sku": "b-205", "title": "Broken Price Example", "amount_cents": "not-a-number", "department": "home"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_b", FIELD_MAPS["source_b"])
        self.assertEqual(ctx.exception.source, "source_b")
        self.assertEqual(ctx.exception.field, "price")

    def test_source_b_missing_field_raises_malformed_record_error(self):
        """A record missing a required source field (amount_cents) raises MalformedRecordError naming that field."""
        raw = {"sku": "b-999", "title": "No Price Field"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_b", FIELD_MAPS["source_b"])
        self.assertEqual(ctx.exception.field, "price")

    def test_source_c_converts_string_price_to_float(self):
        """A well-formed Source C record converts its string-typed price field to a float."""
        raw = {"product_id": "c-301", "product_name": "USB-C Hub", "price": "49.50", "type": "electronics"}
        result = normalize(raw, "source_c", FIELD_MAPS["source_c"])
        self.assertEqual(
            result,
            {
                "source": "source_c",
                "id": "c-301",
                "title": "USB-C Hub",
                "price": 49.50,
                "category": "electronics",
            },
        )


class TestNormalizeTypeRobustness(unittest.TestCase):
    """Uses a synthetic 'source_d' field map (not a real source) to test
    normalize()'s type handling in isolation from any one real source's schema."""

    def test_convertible_type_mismatches_are_coerced(self):
        """Values arriving as a different-but-compatible scalar type (numeric id, numeric category, string price) are coerced, not rejected."""
        raw = {"id": 42, "title": "Widget", "price": "19.99", "category": 7}
        result = normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(
            result,
            {"source": "source_d", "id": "42", "title": "Widget", "price": 19.99, "category": "7"},
        )

    def test_boolean_id_raises_malformed_record_error(self):
        """A boolean id (bool is technically an int subclass in Python) is not a meaningful identifier and is rejected instead of silently becoming 'True'/'False'."""
        raw = {"id": True, "title": "Widget", "price": 9.99, "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "id")

    def test_boolean_price_raises_malformed_record_error(self):
        """A boolean price (float(True) == 1.0) is not a meaningful price and is rejected instead of silently coerced."""
        raw = {"id": "d-1", "title": "Widget", "price": False, "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "price")

    def test_null_field_raises_malformed_record_error(self):
        """A field that's explicitly null is malformed, not silently coerced into the literal string 'None'."""
        raw = {"id": "d-1", "title": None, "price": 9.99, "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "title")

    def test_nested_structure_raises_malformed_record_error(self):
        """A field holding a dict/list instead of a scalar is rejected, not stringified."""
        raw = {"id": "d-1", "title": {"unexpected": "structure"}, "price": 9.99, "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "title")

    def test_nan_price_raises_malformed_record_error(self):
        """NaN is a 'valid' float to Python but would silently corrupt price math downstream, so it's rejected."""
        raw = {"id": "d-1", "title": "Widget", "price": "nan", "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "price")

    def test_infinite_price_raises_malformed_record_error(self):
        """Infinity is rejected the same way as NaN: technically a float, never a real price."""
        raw = {"id": "d-1", "title": "Widget", "price": "inf", "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "price")

    def test_negative_price_raises_malformed_record_error(self):
        """A negative price is a business-rule violation (not a type error) but is still rejected via the price-specific caster."""
        raw = {"id": "d-1", "title": "Widget", "price": -5.0, "category": "misc"}
        with self.assertRaises(MalformedRecordError) as ctx:
            normalize(raw, "source_d", SOURCE_D_FIELD_MAP)
        self.assertEqual(ctx.exception.field, "price")


if __name__ == "__main__":
    unittest.main()
