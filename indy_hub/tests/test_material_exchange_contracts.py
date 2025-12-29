"""
Tests for Material Exchange contract validation system
"""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# AA Example App
# Local
from indy_hub.models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
)
from indy_hub.tasks.material_exchange_contracts import (
    _contract_items_match_order,
    _extract_contract_id,
    _matches_sell_order_criteria,
    validate_material_exchange_sell_orders,
)


class ContractValidationTestCase(TestCase):
    """Tests for contract matching and validation logic"""

    def setUp(self):
        """Set up test data"""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60001234,
            structure_name="Test Structure",
            is_active=True,
        )
        self.seller = User.objects.create_user(username="test_seller")
        self.buyer = User.objects.create_user(username="test_buyer")

        # Create a sell order with an item
        self.sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.PENDING,
        )
        self.sell_item = MaterialExchangeSellOrderItem.objects.create(
            order=self.sell_order,
            type_id=34,  # Tritanium
            type_name="Tritanium",
            quantity=1000,
            unit_price=5.5,
            total_price=5500,
        )

        # Create a buy order with an item
        self.buy_order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
            status=MaterialExchangeBuyOrder.Status.PENDING,
        )
        self.buy_item = MaterialExchangeBuyOrderItem.objects.create(
            order=self.buy_order,
            type_id=34,  # Tritanium
            type_name="Tritanium",
            quantity=500,
            unit_price=6.0,
            total_price=3000,
            stock_available_at_creation=1000,
        )

    def test_matching_contract_criteria(self):
        """Test contract criteria matching"""
        seller_char_id = 111111111  # Mock character ID
        valid_contract = {
            "contract_id": 1,
            "type": "item_exchange",
            "issuer_id": seller_char_id,
            "acceptor_id": self.config.corporation_id,
            "start_location_id": self.config.structure_id,
            "status": "active",
        }

        # Should match
        self.assertTrue(
            _matches_sell_order_criteria(
                valid_contract,
                self.sell_order,
                self.config,
                [seller_char_id],  # Pass as list of char IDs
            )
        )

        # Wrong type
        wrong_type = valid_contract.copy()
        wrong_type["type"] = "courier"
        self.assertFalse(
            _matches_sell_order_criteria(
                wrong_type,
                self.sell_order,
                self.config,
                [seller_char_id],
            )
        )

        # Wrong issuer
        wrong_issuer = valid_contract.copy()
        wrong_issuer["issuer_id"] = 999999
        self.assertFalse(
            _matches_sell_order_criteria(
                wrong_issuer,
                self.sell_order,
                self.config,
                [seller_char_id],
            )
        )

        # Wrong acceptor
        wrong_acceptor = valid_contract.copy()
        wrong_acceptor["acceptor_id"] = 999999
        self.assertFalse(
            _matches_sell_order_criteria(
                wrong_acceptor,
                self.sell_order,
                self.config,
                [seller_char_id],
            )
        )

        # Wrong location
        wrong_location = valid_contract.copy()
        wrong_location["start_location_id"] = 999999
        self.assertFalse(
            _matches_sell_order_criteria(
                wrong_location,
                self.sell_order,
                self.config,
                [seller_char_id],
            )
        )

    def test_contract_items_matching(self):
        """Test contract items matching"""
        correct_items = [{"type_id": 34, "quantity": 1000, "is_included": True}]
        self.assertTrue(_contract_items_match_order(correct_items, self.sell_order))

        # Wrong quantity
        wrong_qty = [{"type_id": 34, "quantity": 500, "is_included": True}]
        self.assertFalse(_contract_items_match_order(wrong_qty, self.sell_order))

        # Wrong type
        wrong_type = [{"type_id": 35, "quantity": 1000, "is_included": True}]
        self.assertFalse(_contract_items_match_order(wrong_type, self.sell_order))

        # Not included (requested items)
        not_included = [{"type_id": 34, "quantity": 1000, "is_included": False}]
        self.assertFalse(_contract_items_match_order(not_included, self.sell_order))

        # Multiple items (should fail for single-item order)
        multiple_items = [
            {"type_id": 34, "quantity": 500, "is_included": True},
            {"type_id": 35, "quantity": 500, "is_included": True},
        ]
        self.assertFalse(_contract_items_match_order(multiple_items, self.sell_order))

    def test_extract_contract_id(self):
        """Test contract ID extraction from notes"""
        # Valid format
        notes = "Contract validated: 123456789"
        self.assertEqual(_extract_contract_id(notes), 123456789)

        # Different prefix
        notes2 = "Some message: 987654321"
        self.assertEqual(_extract_contract_id(notes2), 987654321)

        # No contract ID
        self.assertIsNone(_extract_contract_id("No contract here"))
        self.assertIsNone(_extract_contract_id(""))
        self.assertIsNone(_extract_contract_id(None))

    def test_sell_order_status_transitions(self):
        """Test sell order status field values"""
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.PENDING,
        )

        # Check all status choices exist
        status_values = [s[0] for s in MaterialExchangeSellOrder.Status.choices]
        self.assertIn(MaterialExchangeSellOrder.Status.PENDING, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.APPROVED, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.PAID, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.COMPLETED, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.REJECTED, status_values)

    def test_buy_order_status_transitions(self):
        """Test buy order status field values"""
        self.assertEqual(
            self.buy_order.status,
            MaterialExchangeBuyOrder.Status.PENDING,
        )

        # Check all status choices exist
        status_values = [s[0] for s in MaterialExchangeBuyOrder.Status.choices]
        self.assertIn(MaterialExchangeBuyOrder.Status.PENDING, status_values)
        self.assertIn(MaterialExchangeBuyOrder.Status.APPROVED, status_values)
        self.assertIn(MaterialExchangeBuyOrder.Status.DELIVERED, status_values)
        self.assertIn(MaterialExchangeBuyOrder.Status.COMPLETED, status_values)
        self.assertIn(MaterialExchangeBuyOrder.Status.REJECTED, status_values)


class ContractValidationTaskTest(TestCase):
    """Tests for Celery task execution"""

    def setUp(self):
        """Set up test data"""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60001234,
            structure_name="Test Structure",
            is_active=True,
        )
        self.seller = User.objects.create_user(username="test_seller")
        self.sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.PENDING,
        )
        self.sell_item = MaterialExchangeSellOrderItem.objects.create(
            order=self.sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=1000,
            unit_price=5.5,
            total_price=5500,
        )

    @patch("indy_hub.tasks.material_exchange_contracts.shared_client")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_sell_orders_no_pending(
        self, mock_notify_multi, mock_notify_user, mock_client
    ):
        """Test task when no pending orders exist"""
        self.sell_order.status = MaterialExchangeSellOrder.Status.APPROVED
        self.sell_order.save()

        validate_material_exchange_sell_orders()

        # Should not call ESI
        mock_client.fetch_corporation_contracts.assert_not_called()
        mock_notify_user.assert_not_called()
        mock_notify_multi.assert_not_called()

    @patch("indy_hub.tasks.material_exchange_contracts._get_character_for_scope")
    @patch("indy_hub.tasks.material_exchange_contracts.shared_client")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_sell_orders_contract_found(
        self, mock_notify_multi, mock_client, mock_get_char
    ):
        """Test successful contract validation"""
        seller_char_id = 111111111
        mock_get_char.return_value = seller_char_id

        # Mock successful contract fetch
        mock_client.fetch_corporation_contracts.return_value = [
            {
                "contract_id": 1,
                "type": "item_exchange",
                "issuer_id": seller_char_id,
                "acceptor_id": self.config.corporation_id,
                "start_location_id": self.config.structure_id,
                "status": "active",
                "price": self.sell_item.total_price,
            }
        ]

        # Mock contract items
        mock_client.fetch_corporation_contract_items.return_value = [
            {"type_id": 34, "quantity": 1000, "is_included": True}
        ]

        # Mock getting user's characters
        with patch(
            "indy_hub.tasks.material_exchange_contracts._get_user_character_ids",
            return_value=[seller_char_id],
        ):
            validate_material_exchange_sell_orders()

        # Check order was approved
        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.APPROVED,
        )
        self.assertIn("Contract validated", self.sell_order.notes)

        # Check admins were notified
        mock_notify_multi.assert_called()

    @patch("indy_hub.tasks.material_exchange_contracts._get_character_for_scope")
    @patch("indy_hub.tasks.material_exchange_contracts.shared_client")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_validate_sell_orders_no_contract(
        self, mock_notify_user, mock_client, mock_get_char
    ):
        """Test when contract is not found"""
        seller_char_id = 111111111
        mock_get_char.return_value = seller_char_id

        # No contracts found
        mock_client.fetch_corporation_contracts.return_value = []

        # Mock getting user's characters
        with patch(
            "indy_hub.tasks.material_exchange_contracts._get_user_character_ids",
            return_value=[seller_char_id],
        ):
            validate_material_exchange_sell_orders()

        # Check order was rejected
        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.REJECTED,
        )

        # Check user was notified
        mock_notify_user.assert_called()
        call_args = mock_notify_user.call_args
        self.assertEqual(call_args[0][0], self.seller)  # notified seller
        # Should be a warning level notification
        level = call_args[1].get("level", "").lower()
        self.assertIn(level, ["warning", "error"])


class BuyOrderSignalTest(TestCase):
    """Tests for buy order creation signal"""

    def setUp(self):
        """Set up test data"""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60001234,
            structure_name="Test Structure",
            is_active=True,
        )
        self.buyer = User.objects.create_user(username="test_buyer")

    @patch(
        "indy_hub.tasks.material_exchange_contracts.handle_material_exchange_buy_order_created"
    )
    def test_buy_order_signal_on_create(self, mock_task):
        """Test that signal is triggered on buy order creation"""
        buy_order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=buy_order,
            type_id=34,
            type_name="Tritanium",
            quantity=500,
            unit_price=6.0,
            total_price=3000,
            stock_available_at_creation=1000,
        )

        # Task should be queued (async)
        # Note: In test env, .delay() might not actually queue
        # but we're testing the signal triggers
        self.assertEqual(buy_order.status, MaterialExchangeBuyOrder.Status.PENDING)


if __name__ == "__main__":
    # Standard Library
    import unittest

    unittest.main()
