import unittest

from aggregator.normalize import MalformedRecordError, normalize
from aggregator.sources import FIELD_MAPS


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


if __name__ == "__main__":
    unittest.main()
