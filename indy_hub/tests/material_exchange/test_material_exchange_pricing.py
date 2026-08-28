"""
Tests for Material Exchange pricing with configurable base prices.
"""

# Standard Library
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

# AA Example App
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeStock
from indy_hub.utils.material_exchange_pricing import get_sell_price_override
from indy_hub.views.material_exchange import (
    _has_reliable_sell_reference_price,
    material_exchange_index,
)


class MaterialExchangePricingTests(TestCase):
    """Test price calculations with different base price configurations."""

    def setUp(self):
        """Create test config and stock item."""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=60003760,
            structure_name="Test Structure",
            hangar_division=1,
            sell_markup_percent=Decimal("5.00"),
            sell_markup_base="buy",  # Default: Sell orders based on Jita Buy
            buy_markup_percent=Decimal("10.00"),
            buy_markup_base="buy",  # Default: Buy orders based on Jita Buy
        )

        self.stock = MaterialExchangeStock.objects.create(
            config=self.config,
            type_id=34,  # Tritanium
            type_name="Tritanium",
            quantity=1000000,
            jita_buy_price=Decimal("5.00"),
            jita_sell_price=Decimal("6.00"),
        )

    def test_member_buys_from_hub_using_jita_buy_base(self):
        """Test sell_price_to_member when using Jita Buy as base."""
        # Config: buy_markup_base = "buy", buy_markup_percent = 10%
        # Expected: 5.00 * 1.10 = 5.50
        expected = Decimal("5.50")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_member_buys_from_hub_using_jita_sell_base(self):
        """Test sell_price_to_member when using Jita Sell as base."""
        self.config.buy_markup_base = "sell"
        self.config.save()

        # Expected: 6.00 * 1.10 = 6.60
        expected = Decimal("6.60")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_member_sells_to_hub_using_jita_buy_base(self):
        """Test buy_price_from_member when using Jita Buy as base."""
        # Config: sell_markup_base = "buy", sell_markup_percent = 5%
        # Expected: 5.00 * 1.05 = 5.25
        expected = Decimal("5.25")
        actual = self.stock.buy_price_from_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_member_sells_to_hub_using_jita_sell_base(self):
        """Test buy_price_from_member when using Jita Sell as base."""
        self.config.sell_markup_base = "sell"
        self.config.save()

        # Expected: 6.00 * 1.05 = 6.30
        expected = Decimal("6.30")
        actual = self.stock.buy_price_from_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_member_sell_fixed_price_override_bypasses_missing_jita_data(self):
        self.config.sell_price_overrides = {str(self.stock.type_id): "42.50"}
        self.stock.jita_buy_price = Decimal("0")
        self.stock.jita_sell_price = Decimal("0")

        self.assertEqual(self.stock.buy_price_from_member, Decimal("42.50"))

    def test_member_sell_fixed_price_override_is_scoped_to_exact_type(self):
        self.config.sell_price_overrides = {"74534": "4500.00"}

        self.assertEqual(self.stock.buy_price_from_member, Decimal("5.25"))

    def test_member_sell_fixed_price_override_normalizes_admin_json_precision(self):
        self.config.sell_price_overrides = {str(self.stock.type_id): "0.006"}

        self.assertEqual(
            get_sell_price_override(config=self.config, type_id=self.stock.type_id),
            Decimal("0.01"),
        )

    def test_member_sell_fixed_price_override_rejects_value_rounded_to_zero(self):
        self.config.sell_price_overrides = {str(self.stock.type_id): "0.001"}

        self.assertIsNone(
            get_sell_price_override(config=self.config, type_id=self.stock.type_id)
        )

    def test_member_sell_fixed_price_override_rejects_database_overflow(self):
        self.config.sell_price_overrides = {
            str(self.stock.type_id): "999999999999999999.999"
        }

        self.assertIsNone(
            get_sell_price_override(config=self.config, type_id=self.stock.type_id)
        )

    def test_reliable_sell_reference_price_matrix(self):
        self.assertFalse(
            _has_reliable_sell_reference_price(
                jita_buy=Decimal("0"), jita_sell=Decimal("6")
            )
        )
        self.assertFalse(
            _has_reliable_sell_reference_price(
                jita_buy=Decimal("5"), jita_sell=Decimal("0")
            )
        )
        self.assertTrue(
            _has_reliable_sell_reference_price(
                jita_buy=Decimal("5"), jita_sell=Decimal("6")
            )
        )
        self.assertTrue(
            _has_reliable_sell_reference_price(
                jita_buy=Decimal("0"),
                jita_sell=Decimal("0"),
                configured_price=Decimal("42.50"),
            )
        )

    def test_zero_markup_on_buy_base(self):
        """Test that 0% markup returns base price exactly."""
        self.config.buy_markup_percent = Decimal("0.00")
        self.config.buy_markup_base = "buy"
        self.config.save()

        # Expected: 5.00 * 1.00 = 5.00
        expected = Decimal("5.00")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_zero_markup_on_sell_base(self):
        """Test that 0% markup returns base price exactly."""
        self.config.sell_markup_percent = Decimal("0.00")
        self.config.sell_markup_base = "sell"
        self.config.save()

        # Expected: 6.00 * 1.00 = 6.00
        expected = Decimal("6.00")
        actual = self.stock.buy_price_from_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_high_markup_calculation(self):
        """Test with higher markup percentage."""
        self.config.buy_markup_percent = Decimal("25.00")
        self.config.buy_markup_base = "sell"
        self.config.save()

        # Expected: 6.00 * 1.25 = 7.50
        expected = Decimal("7.50")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_negative_markup_reduces_member_sell_price(self):
        """Negative markup should discount the configured base price."""
        self.config.buy_markup_percent = Decimal("-10.00")
        self.config.buy_markup_base = "buy"
        self.config.save()

        expected = Decimal("4.50")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_default_values_are_buy(self):
        """Test that default markup base is 'buy' for both settings."""
        new_config = MaterialExchangeConfig.objects.create(
            corporation_id=999999,
            structure_id=60003760,
            structure_name="New Test Structure",
            hangar_division=2,
        )

        self.assertEqual(new_config.sell_markup_base, "buy")
        self.assertEqual(new_config.buy_markup_base, "buy")
        self.assertFalse(new_config.enforce_jita_price_bounds)

    def test_bounds_clamp_sell_base_negative_floors_at_buy(self):
        """When enabled, Jita Sell + negative % cannot go below Jita Buy."""
        self.config.enforce_jita_price_bounds = True
        self.config.buy_markup_base = "sell"
        self.config.buy_markup_percent = Decimal("-50.00")
        self.config.save()

        # Base sell is 6.00; -50% would be 3.00, but floor is Jita Buy (5.00)
        expected = Decimal("5.00")
        actual = self.stock.sell_price_to_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)

    def test_bounds_clamp_buy_base_positive_caps_at_sell(self):
        """When enabled, Jita Buy + positive % cannot go above Jita Sell."""
        self.config.enforce_jita_price_bounds = True
        self.config.sell_markup_base = "buy"
        self.config.sell_markup_percent = Decimal("50.00")
        self.config.save()

        # Base buy is 5.00; +50% would be 7.50, but cap is Jita Sell (6.00)
        expected = Decimal("6.00")
        actual = self.stock.buy_price_from_member
        self.assertAlmostEqual(float(actual), float(expected), places=2)


class MaterialExchangeIndexPricingTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="index-pricing")
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=60003760,
            structure_name="Test Structure",
            hangar_division=1,
            sell_markup_base="buy",
            sell_price_overrides={"35": "42.50"},
        )

    def test_stats_exclude_unreliable_prices_but_include_fixed_overrides(self) -> None:
        request = self.factory.get("/indy_hub/material-exchange/")
        request.user = self.user
        view = material_exchange_index
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_accepted_locations",
                return_value=[],
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_location_ids",
                return_value=[self.config.structure_id],
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_location_summary",
                return_value="Test Structure",
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure",
                return_value=({34: 2, 35: 3}, False),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34, 35},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={
                    34: {"buy": Decimal("5.00"), "sell": Decimal("0")},
                    35: {"buy": Decimal("0"), "sell": Decimal("0")},
                },
            ),
            patch(
                "indy_hub.views.material_exchange._build_nav_context",
                return_value={},
            ),
            patch(
                "indy_hub.views.material_exchange.build_nav_context", return_value={}
            ),
            patch(
                "indy_hub.views.material_exchange.render",
                return_value=HttpResponse(),
            ) as mock_render,
        ):
            response = view(request)

        self.assertEqual(response.status_code, 200)
        context = mock_render.call_args.args[2]
        self.assertEqual(context["stock_count"], 1)
        self.assertEqual(context["total_stock_value"], Decimal("127.50"))
