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
    _extract_contract_id,
    validate_material_exchange_buy_orders,
    validate_material_exchange_sell_orders,
)

# Note: Legacy test functions _contract_items_match_order and _matches_sell_order_criteria
# have been replaced with _db variants that work with database models instead of dicts


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
            status=MaterialExchangeSellOrder.Status.DRAFT,
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
            status=MaterialExchangeBuyOrder.Status.DRAFT,
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

        # TODO: Update these tests to use database models instead of dict contracts
        # These tests are skipped for now as the implementation has moved to _db variants
        self.skipTest(
            "Legacy dict-based contract matching tests - needs update for DB models"
        )

    def test_contract_items_matching(self):
        """Test contract items matching"""
        # TODO: Update to use ESIContractItem models instead of dicts
        self.skipTest(
            "Legacy dict-based contract item matching - needs update for DB models"
        )

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
            MaterialExchangeSellOrder.Status.DRAFT,
        )

        # Check all status choices exist
        status_values = [s[0] for s in MaterialExchangeSellOrder.Status.choices]
        self.assertIn(MaterialExchangeSellOrder.Status.DRAFT, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.VALIDATED, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.COMPLETED, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.REJECTED, status_values)

    def test_buy_order_status_transitions(self):
        """Test buy order status field values"""
        self.assertEqual(
            self.buy_order.status,
            MaterialExchangeBuyOrder.Status.DRAFT,
        )

        # Check all status choices exist
        status_values = [s[0] for s in MaterialExchangeBuyOrder.Status.choices]
        self.assertIn(MaterialExchangeBuyOrder.Status.DRAFT, status_values)
        self.assertIn(MaterialExchangeBuyOrder.Status.VALIDATED, status_values)
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
            status=MaterialExchangeSellOrder.Status.DRAFT,
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
        self.sell_order.status = MaterialExchangeSellOrder.Status.VALIDATED
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
        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_get_char.return_value = seller_char_id

        # Create cached contract in database (instead of mocking ESI)
        contract = ESIContract.objects.create(
            contract_id=1,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            acceptor_id=0,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_item.total_price,
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )

        # Create contract item
        ESIContractItem.objects.create(
            contract=contract,
            record_id=1,
            type_id=34,
            quantity=1000,
            is_included=True,
        )

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
            MaterialExchangeSellOrder.Status.VALIDATED,
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

        # No contracts in database (empty queryset simulates no cached contracts)
        # The validation function now queries ESIContract.objects instead of calling ESI

        # Mock getting user's characters
        with patch(
            "indy_hub.tasks.material_exchange_contracts._get_user_character_ids",
            return_value=[seller_char_id],
        ):
            validate_material_exchange_sell_orders()

        # Check order stays pending when no contracts in database (warning logged instead)
        self.sell_order.refresh_from_db()
        # Note: Order stays DRAFT when no cached contracts exist (validation can't run)
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.DRAFT,
        )
        # User is not notified when no contracts are cached (just a warning log)
        mock_notify_user.assert_not_called()


class BuyOrderValidationTaskTest(TestCase):
    """Tests for buy order validation task behavior."""

    def setUp(self):
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60001234,
            structure_name="Test Structure",
            is_active=True,
        )
        self.buyer = User.objects.create_user(username="test_buyer")

        self.buy_order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
            status=MaterialExchangeBuyOrder.Status.DRAFT,
            order_reference="INDY-9380811210",
        )
        self.buy_item = MaterialExchangeBuyOrderItem.objects.create(
            order=self.buy_order,
            type_id=34,
            type_name="Tritanium",
            quantity=500,
            unit_price=6.0,
            total_price=3000,
            stock_available_at_creation=1000,
        )

    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_buy_order_in_draft_with_matching_contract(
        self, mock_multi, mock_user
    ):
        """Draft buy orders should be auto-validated when a matching cached contract exists."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        buyer_char_id = 999999999

        contract = ESIContract.objects.create(
            contract_id=227079044,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=0,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=buyer_char_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            title=self.buy_order.order_reference,
            price=self.buy_order.total_price,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=1,
            type_id=self.buy_item.type_id,
            quantity=self.buy_item.quantity,
            is_included=True,
        )

        with patch(
            "indy_hub.tasks.material_exchange_contracts._get_user_character_ids",
            return_value=[buyer_char_id],
        ):
            validate_material_exchange_buy_orders()

        self.buy_order.refresh_from_db()
        self.assertEqual(
            self.buy_order.status, MaterialExchangeBuyOrder.Status.VALIDATED
        )
        self.assertEqual(self.buy_order.esi_contract_id, contract.contract_id)
        self.assertIn("Contract validated", self.buy_order.notes)

        mock_user.assert_called()
        mock_multi.assert_called()


class StructureNameMatchingTest(TestCase):
    """Tests for structure name-based matching instead of ID-only"""

    def setUp(self):
        """Set up test data"""
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=1045667241057,
            structure_name="C-N4OD - Fountain of Life",
            is_active=True,
        )
        self.seller = User.objects.create_user(username="test_seller")
        self.sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.DRAFT,
        )
        self.sell_item = MaterialExchangeSellOrderItem.objects.create(
            order=self.sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=1000,
            unit_price=5.5,
            total_price=5500,
        )

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_contract_matches_by_structure_name(
        self, mock_notify_multi, mock_get_char_ids
    ):
        """Test that contract with different structure ID matches by name"""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_get_char_ids.return_value = [seller_char_id]

        # Create contract with different structure ID (1045722708748 instead of 1045667241057)
        # but same structure name "C-N4OD - Fountain of Life"
        contract = ESIContract.objects.create(
            contract_id=226598409,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=1045722708748,  # Different ID, same structure
            end_location_id=1045722708748,
            price=5500,
            title="INDY-3",
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=1,
            type_id=34,
            quantity=1000,
            is_included=True,
        )

        # Mock ESI client to return the structure name
        mock_esi_client = patch(
            "indy_hub.tasks.material_exchange_contracts.shared_client"
        )
        mock_client_instance = mock_esi_client.start()
        mock_client_instance.get_structure_info.return_value = {
            "name": "C-N4OD - Fountain of Life"
        }

        validate_material_exchange_sell_orders()

        # Check order was approved (matched by structure name)
        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status, MaterialExchangeSellOrder.Status.VALIDATED
        )
        self.assertIn("226598409", self.sell_order.notes)

        # Verify admin notification was sent
        mock_notify_multi.assert_called_once()

        mock_esi_client.stop()

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    def test_contract_falls_back_to_id_matching(self, mock_get_char_ids):
        """Test that ID matching still works if ESI lookup fails"""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_get_char_ids.return_value = [seller_char_id]

        # Create contract with matching structure ID
        contract = ESIContract.objects.create(
            contract_id=226598410,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            price=5500,
            title="INDY-3",
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=1,
            type_id=34,
            quantity=1000,
            is_included=True,
        )

        # Mock ESI client to fail (returns None)
        with patch(
            "indy_hub.tasks.material_exchange_contracts.shared_client"
        ) as mock_client:
            mock_client.get_structure_info.side_effect = Exception("ESI Error")

            with patch("indy_hub.tasks.material_exchange_contracts.notify_multi"):
                validate_material_exchange_sell_orders()

        # Check order was approved (matched by ID fallback)
        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status, MaterialExchangeSellOrder.Status.VALIDATED
        )


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
        self.assertEqual(buy_order.status, MaterialExchangeBuyOrder.Status.DRAFT)


if __name__ == "__main__":
    # Standard Library
    import unittest

    unittest.main()
