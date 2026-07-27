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
from unittest.mock import patch

# AA Example App
from indy_hub.views.industry import _build_copy_estimated_item_values


def _make_request(type_id: int, runs_requested: int = 1):
    return SimpleNamespace(type_id=type_id, runs_requested=runs_requested)


class _CursorStub:
    def __init__(self, product_rows, material_rows):
        self._product_rows = list(product_rows)
        self._material_rows = list(material_rows)
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, _params=None):
        normalized = " ".join(str(sql).split()).lower()
        if "from eve_sde_blueprintactivityproduct" in normalized:
            self._rows = list(self._product_rows)
        elif "from eve_sde_blueprintactivitymaterial" in normalized:
            self._rows = list(self._material_rows)
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class CopyEstimatedItemValueFromMaterialsTests(TestCase):
    """EIV must be derived from manufacturing materials, not the product."""

    def setUp(self) -> None:
        # Antimatter Charge M Blueprint -> produces type 222.
        self.blueprint_type_id = 220
        self.product_type_id = 222
        # Two materials at adjusted_price; Σ qty * price = 14_507 / run.
        self.materials = [
            (self.blueprint_type_id, 34, 1000),  # Tritanium
            (self.blueprint_type_id, 36, 200),  # Pyerite
        ]
        self.products = [(self.blueprint_type_id, self.product_type_id)]
        self.adjusted_prices = {
            34: {"adjusted_price": Decimal("10"), "average_price": Decimal("11")},
            36: {"adjusted_price": Decimal("22.535"), "average_price": Decimal("22")},
        }
        self.expected_per_run = Decimal("1000") * Decimal("10") + Decimal(
            "200"
        ) * Decimal("22.535")

    def _patches(self):
        cursor_stub = _CursorStub(self.products, self.materials)
        return (
            patch(
                "indy_hub.views.industry.connection.cursor",
                return_value=cursor_stub,
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
        with patches[0], patches[1], patches[2]:
            result = _build_copy_estimated_item_values([req])

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
        with patches[0], patches[1], patches[2]:
            result = _build_copy_estimated_item_values([req])

        entry = result[self.blueprint_type_id]
        expected = Decimal("1000") * Decimal("12") + Decimal("200") * Decimal("22.535")
        self.assertEqual(entry["unit_value"], expected)
        # The basket's reported source is the weakest source actually used.
        self.assertEqual(entry["source"], "average_price")

    def test_blueprint_with_no_materials_is_skipped(self) -> None:
        req = _make_request(self.blueprint_type_id, runs_requested=1)
        self.materials = []
        patches = self._patches()
        with patches[0], patches[1], patches[2]:
            result = _build_copy_estimated_item_values([req])

        self.assertEqual(result, {})
