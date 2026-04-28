"""Regression tests for copy installation-cost EIV computation.

EVE Online computes the Estimated Item Value (EIV) for a copying job from
the blueprint's *manufacturing* materials (sum of material_qty *
adjusted_price), not from the product's adjusted_price. Indy_Hub previously
computed EIV from the product price, which dramatically under-reported the
install cost for cheap products built from expensive materials (e.g. ammo,
charges).
"""

# Standard Library
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

# AA Example App
from indy_hub.views.industry import _build_copy_estimated_item_values


def _make_request(type_id: int, runs_requested: int = 1):
    return SimpleNamespace(type_id=type_id, runs_requested=runs_requested)


class _FilterStub:
    """Mimic enough of QuerySet.filter().values() / .order_by() for tests."""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, **_kwargs):
        return self

    def values(self, *_fields):
        return list(self._rows)

    def order_by(self, *_fields):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class CopyEstimatedItemValueFromMaterialsTests(TestCase):
    """EIV must be derived from manufacturing materials, not the product."""

    def setUp(self) -> None:
        # Antimatter Charge M Blueprint -> produces type 222.
        self.blueprint_type_id = 220
        self.product_type_id = 222
        # Two materials at adjusted_price; Σ qty * price = 14_507 / run.
        self.materials = [
            {
                "eve_type_id": self.blueprint_type_id,
                "material_eve_type_id": 34,  # Tritanium
                "quantity": 1000,
            },
            {
                "eve_type_id": self.blueprint_type_id,
                "material_eve_type_id": 36,  # Pyerite
                "quantity": 200,
            },
        ]
        self.products = [
            SimpleNamespace(
                eve_type_id=self.blueprint_type_id,
                product_eve_type_id=self.product_type_id,
            )
        ]
        self.adjusted_prices = {
            34: {"adjusted_price": Decimal("10"), "average_price": Decimal("11")},
            36: {"adjusted_price": Decimal("22.535"), "average_price": Decimal("22")},
        }
        self.expected_per_run = Decimal("1000") * Decimal("10") + Decimal(
            "200"
        ) * Decimal("22.535")

    def _patches(self):
        return (
            patch(
                "indy_hub.views.industry.SDEBlueprintActivityMaterial.objects",
                MagicMock(filter=lambda **_kw: _FilterStub(self.materials)),
            ),
            patch(
                "indy_hub.views.industry.SDEBlueprintActivityProduct.objects",
                MagicMock(filter=lambda **_kw: _FilterStub(self.products)),
            ),
            patch(
                "indy_hub.views.industry._fetch_item_base_prices",
                return_value={},
            ),
            patch(
                "indy_hub.views.industry.fetch_adjusted_prices",
                return_value=self.adjusted_prices,
            ),
        )

    def test_eiv_uses_manufacturing_materials_not_product_price(self) -> None:
        req = _make_request(self.blueprint_type_id, runs_requested=20)
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = _build_copy_estimated_item_values([req])
        finally:
            for p in patches:
                p.stop()

        self.assertIn(self.blueprint_type_id, result)
        entry = result[self.blueprint_type_id]
        self.assertEqual(entry["unit_value"], self.expected_per_run)
        self.assertEqual(entry["estimated_item_value"], self.expected_per_run * 20)
        self.assertEqual(entry["runs_requested"], 20)
        self.assertEqual(entry["product_type_id"], self.product_type_id)
        self.assertEqual(entry["source"], "adjusted_price")

    def test_falls_back_to_average_price_when_adjusted_missing(self) -> None:
        req = _make_request(self.blueprint_type_id, runs_requested=1)
        # Drop adjusted_price for one material; rely on average_price.
        self.adjusted_prices[34] = {
            "adjusted_price": Decimal("0"),
            "average_price": Decimal("12"),
        }
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = _build_copy_estimated_item_values([req])
        finally:
            for p in patches:
                p.stop()

        entry = result[self.blueprint_type_id]
        expected = Decimal("1000") * Decimal("12") + Decimal("200") * Decimal("22.535")
        self.assertEqual(entry["unit_value"], expected)
        # The basket's reported source is the weakest source actually used.
        self.assertEqual(entry["source"], "average_price")

    def test_blueprint_with_no_materials_is_skipped(self) -> None:
        req = _make_request(self.blueprint_type_id, runs_requested=1)
        self.materials = []
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = _build_copy_estimated_item_values([req])
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(result, {})
