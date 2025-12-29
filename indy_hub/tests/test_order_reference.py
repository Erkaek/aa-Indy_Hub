"""
Test for order_reference auto-generation.
"""

# Standard Library
import unittest

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# Local
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeSellOrder


class OrderReferenceTestCase(TestCase):
    """Test order_reference field auto-generation."""

    @classmethod
    def setUpTestData(cls):
        """Set up test fixtures."""
        cls.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        cls.config = MaterialExchangeConfig.objects.create(
            name="Test Hub",
            is_active=True,
            corporation_id=98765432,
            structure_id=60000001,
            structure_name="Test Station",
            buy_markup_percent=5,
            sell_markup_percent=5,
        )

    def test_order_reference_auto_generated(self):
        """Order reference should be auto-generated on save."""
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status="pending",
        )
        
        # Refresh from DB to ensure save completed
        order.refresh_from_db()
        
        # Check that order_reference was generated
        self.assertIsNotNone(order.order_reference)
        self.assertEqual(order.order_reference, f"INDY-{order.id}")
        self.assertTrue(order.order_reference.startswith("INDY-"))

    def test_order_reference_unique(self):
        """Order references should be unique per order."""
        order1 = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status="pending",
        )
        order2 = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status="pending",
        )
        
        order1.refresh_from_db()
        order2.refresh_from_db()
        
        # Each should have a different reference
        self.assertNotEqual(order1.order_reference, order2.order_reference)
        self.assertEqual(order1.order_reference, f"INDY-{order1.id}")
        self.assertEqual(order2.order_reference, f"INDY-{order2.id}")

    def test_order_reference_not_overwritten(self):
        """Existing order_reference should not be overwritten on save."""
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status="pending",
        )
        order.refresh_from_db()
        original_ref = order.order_reference
        
        # Save again
        order.status = "approved"
        order.save()
        order.refresh_from_db()
        
        # Reference should remain the same
        self.assertEqual(order.order_reference, original_ref)


if __name__ == "__main__":
    unittest.main()
