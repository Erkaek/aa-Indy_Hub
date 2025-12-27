"""
Tests for Material Exchange pricing with configurable base prices.
"""
from decimal import Decimal
from django.test import TestCase
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeStock


class MaterialExchangePricingTests(TestCase):
    """Test price calculations with different base price configurations."""

    def setUp(self):
        """Create test config and stock item."""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=789012,
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

    def test_default_values_are_buy(self):
        """Test that default markup base is 'buy' for both settings."""
        new_config = MaterialExchangeConfig.objects.create(
            corporation_id=999999,
            structure_id=888888,
            structure_name="New Test Structure",
            hangar_division=2,
        )
        
        self.assertEqual(new_config.sell_markup_base, "buy")
        self.assertEqual(new_config.buy_markup_base, "buy")
