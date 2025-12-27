#!/usr/bin/env python
"""
Quick test script to verify price base calculation logic.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testauth.settings.test")
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from decimal import Decimal
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeStock


def test_price_calculations():
    """Test that price calculations work with different base price settings."""
    
    # Create a test config
    config = MaterialExchangeConfig.objects.create(
        corporation_id=123456,
        structure_id=789012,
        structure_name="Test Structure",
        hangar_division=1,
        sell_markup_percent=Decimal("5.00"),
        sell_markup_base="buy",  # Sell orders based on Jita Buy
        buy_markup_percent=Decimal("10.00"),
        buy_markup_base="sell",  # Buy orders based on Jita Sell
    )
    
    # Create a test stock item
    stock = MaterialExchangeStock.objects.create(
        config=config,
        type_id=34,  # Tritanium
        type_name="Tritanium",
        quantity=1000000,
        jita_buy_price=Decimal("5.00"),
        jita_sell_price=Decimal("6.00"),
    )
    
    # Test 1: sell_price_to_member (member buys FROM hub)
    # Should use Jita Sell + 10% = 6.00 * 1.10 = 6.60
    expected_sell = Decimal("6.60")
    actual_sell = stock.sell_price_to_member
    print(f"✓ Test 1: Member buys from hub")
    print(f"  Base: Jita Sell = {stock.jita_sell_price}")
    print(f"  Markup: {config.buy_markup_percent}%")
    print(f"  Expected: {expected_sell}")
    print(f"  Actual: {actual_sell}")
    assert abs(actual_sell - expected_sell) < Decimal("0.01"), f"Expected {expected_sell}, got {actual_sell}"
    print(f"  ✓ PASS\n")
    
    # Test 2: buy_price_from_member (member sells TO hub)
    # Should use Jita Buy + 5% = 5.00 * 1.05 = 5.25
    expected_buy = Decimal("5.25")
    actual_buy = stock.buy_price_from_member
    print(f"✓ Test 2: Member sells to hub")
    print(f"  Base: Jita Buy = {stock.jita_buy_price}")
    print(f"  Markup: {config.sell_markup_percent}%")
    print(f"  Expected: {expected_buy}")
    print(f"  Actual: {actual_buy}")
    assert abs(actual_buy - expected_buy) < Decimal("0.01"), f"Expected {expected_buy}, got {actual_buy}"
    print(f"  ✓ PASS\n")
    
    # Test 3: Change config to use sell for both
    config.sell_markup_base = "sell"
    config.save()
    stock.refresh_from_db()
    
    # Now sell orders should use Jita Sell + 5% = 6.00 * 1.05 = 6.30
    expected_buy_sell = Decimal("6.30")
    actual_buy_sell = stock.buy_price_from_member
    print(f"✓ Test 3: Member sells to hub (using Jita Sell base)")
    print(f"  Base: Jita Sell = {stock.jita_sell_price}")
    print(f"  Markup: {config.sell_markup_percent}%")
    print(f"  Expected: {expected_buy_sell}")
    print(f"  Actual: {actual_buy_sell}")
    assert abs(actual_buy_sell - expected_buy_sell) < Decimal("0.01"), f"Expected {expected_buy_sell}, got {actual_buy_sell}"
    print(f"  ✓ PASS\n")
    
    # Cleanup
    stock.delete()
    config.delete()
    
    print("=" * 60)
    print("All tests PASSED! ✓")
    print("=" * 60)


if __name__ == "__main__":
    test_price_calculations()
