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
            structure_id=60003760,
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
        self.assertIn(MaterialExchangeSellOrder.Status.ANOMALY, status_values)
        self.assertIn(MaterialExchangeSellOrder.Status.ANOMALY_REJECTED, status_values)
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
            structure_id=60003760,
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
            title=self.sell_order.order_reference,
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

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_sell_orders_wrong_reference_only_sets_anomaly(
        self, mock_notify_multi, mock_notify_user, mock_user_chars
    ):
        """Strict near-match without title reference must move order to anomaly."""
        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_user_chars.return_value = [seller_char_id]

        near_match_contract = ESIContract.objects.create(
            contract_id=2001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_item.total_price,
            title="WRONG-REF-ONLY",
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=near_match_contract,
            record_id=20011,
            type_id=self.sell_item.type_id,
            quantity=self.sell_item.quantity,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.ANOMALY,
        )
        self.assertIn("title reference is incorrect", self.sell_order.notes)
        self.assertIn("Expected reference", self.sell_order.notes)
        mock_notify_user.assert_called()
        mock_notify_multi.assert_called()

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_sell_orders_wrong_price_has_priority_over_wrong_ref(
        self, mock_notify_multi, mock_notify_user, mock_user_chars
    ):
        """Wrong price with exact reference must win over wrong-reference near-match."""
        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_user_chars.return_value = [seller_char_id]

        wrong_price_exact_ref = ESIContract.objects.create(
            contract_id=3001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_item.total_price + 1,
            title=self.sell_order.order_reference,
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=wrong_price_exact_ref,
            record_id=30011,
            type_id=self.sell_item.type_id,
            quantity=self.sell_item.quantity,
            is_included=True,
        )

        near_match_wrong_ref = ESIContract.objects.create(
            contract_id=3002,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_item.total_price,
            title="NO-ORDER-REFERENCE",
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=near_match_wrong_ref,
            record_id=30021,
            type_id=self.sell_item.type_id,
            quantity=self.sell_item.quantity,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.ANOMALY,
        )
        self.assertIn("wrong price", self.sell_order.notes)
        self.assertNotIn("title reference is incorrect", self.sell_order.notes)
        mock_notify_user.assert_called()
        mock_notify_multi.assert_called()

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_validate_sell_orders_no_match_keeps_order_open(
        self, mock_notify_user, mock_user_chars
    ):
        """When no contract matches sell criteria, order must stay open (not anomaly)."""
        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_user_chars.return_value = [seller_char_id]

        non_matching_contract = ESIContract.objects.create(
            contract_id=4001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_item.total_price,
            title="UNRELATED-CONTRACT",
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=non_matching_contract,
            record_id=40011,
            type_id=35,
            quantity=self.sell_item.quantity,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.DRAFT,
        )
        self.assertIn("Waiting for matching contract", self.sell_order.notes)
        self.assertNotIn("title reference is incorrect", self.sell_order.notes)
        mock_notify_user.assert_not_called()

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    def test_validate_sell_orders_finished_wrong_reference_force_validates(
        self, mock_user_chars
    ):
        """Finished near-match with wrong reference should not stay in anomaly."""
        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 111111111
        mock_user_chars.return_value = [seller_char_id]

        contract = ESIContract.objects.create(
            contract_id=4101,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="finished",
            price=self.sell_item.total_price,
            title="WRONG-REF-FINISHED",
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=41011,
            type_id=self.sell_item.type_id,
            quantity=self.sell_item.quantity,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status,
            MaterialExchangeSellOrder.Status.VALIDATED,
        )
        self.assertEqual(self.sell_order.esi_contract_id, contract.contract_id)
        self.assertIn("accepted in-game despite anomaly", self.sell_order.notes)

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_sell_orders_items_mismatch_notification_includes_deltas(
        self, mock_notify_multi, mock_notify_user, mock_user_chars
    ):
        """Sell mismatch notifications should include exact missing and surplus quantities."""
        # AA Example App
        from indy_hub.models import (
            ESIContract,
            ESIContractItem,
            MaterialExchangeSellOrderItem,
        )

        seller_char_id = 111111111
        mock_user_chars.return_value = [seller_char_id]

        MaterialExchangeSellOrderItem.objects.create(
            order=self.sell_order,
            type_id=37,
            type_name="Isogen",
            quantity=4,
            unit_price=7,
            total_price=28,
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=self.sell_order,
            type_id=35,
            type_name="Pyerite",
            quantity=10,
            unit_price=8,
            total_price=80,
        )

        mismatch_contract = ESIContract.objects.create(
            contract_id=4201,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=self.sell_order.total_price,
            title=self.sell_order.order_reference,
            date_issued="2024-01-01T00:00:00Z",
            date_expired="2024-12-31T23:59:59Z",
        )
        ESIContractItem.objects.create(
            contract=mismatch_contract,
            record_id=42011,
            type_id=34,
            quantity=1000,
            is_included=True,
        )
        ESIContractItem.objects.create(
            contract=mismatch_contract,
            record_id=42012,
            type_id=37,
            quantity=7,
            is_included=True,
        )
        ESIContractItem.objects.create(
            contract=mismatch_contract,
            record_id=42013,
            type_id=35,
            quantity=3,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        self.sell_order.refresh_from_db()
        self.assertEqual(
            self.sell_order.status, MaterialExchangeSellOrder.Status.ANOMALY
        )
        self.assertIn("Missing:", self.sell_order.notes)
        self.assertIn("- 7 Pyerite", self.sell_order.notes)
        self.assertIn("Surplus:", self.sell_order.notes)
        self.assertIn("- 3 Isogen", self.sell_order.notes)

        self.assertTrue(mock_notify_user.called)
        notify_message = mock_notify_user.call_args[0][2]
        self.assertIn("Missing:", notify_message)
        self.assertIn("- 7 Pyerite", notify_message)
        self.assertIn("Surplus:", notify_message)
        self.assertIn("- 3 Isogen", notify_message)
        mock_notify_multi.assert_called()


class BuyOrderValidationTaskTest(TestCase):
    """Tests for buy order validation task behavior."""

    def setUp(self):
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60003760,
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

    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_buy_order_finished_contract_items_mismatch_force_validates(
        self, mock_multi, mock_user
    ):
        """Finished in-game contract with item mismatch should not leave buy order pending."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        buyer_char_id = 999999999

        contract = ESIContract.objects.create(
            contract_id=227079045,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=0,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=buyer_char_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="finished",
            title=self.buy_order.order_reference,
            price=self.buy_order.total_price,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=2,
            type_id=self.buy_item.type_id,
            quantity=self.buy_item.quantity + 1,
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
        self.assertIn("accepted in-game despite anomaly", self.buy_order.notes)

        mock_user.assert_called()
        mock_multi.assert_called()

    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_buy_order_finished_wrong_reference_force_validates(
        self, mock_multi, mock_user
    ):
        """Finished near-match with wrong title reference should not remain pending."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        buyer_char_id = 999999999

        contract = ESIContract.objects.create(
            contract_id=227079046,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=0,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=buyer_char_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="finished",
            title="NO-REF-HERE",
            price=self.buy_order.total_price,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=3,
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
        self.assertIn("accepted in-game despite anomaly", self.buy_order.notes)

        mock_user.assert_called()
        mock_multi.assert_called()

    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    def test_validate_buy_order_finished_criteria_mismatch_force_validates(
        self, mock_multi, mock_user
    ):
        """Finished contract with matching reference but criteria mismatch should not remain pending."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        buyer_char_id = 999999999

        contract = ESIContract.objects.create(
            contract_id=227079047,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=0,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=buyer_char_id,
            start_location_id=70000001,
            end_location_id=70000001,
            status="finished",
            title=self.buy_order.order_reference,
            price=self.buy_order.total_price,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=contract,
            record_id=4,
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
        self.assertIn("accepted in-game despite anomaly", self.buy_order.notes)

        mock_user.assert_called()
        mock_multi.assert_called()

    @patch(
        "indy_hub.tasks.material_exchange_contracts._notify_material_exchange_admins"
    )
    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    def test_validate_buy_order_pending_mismatch_notification_includes_deltas(
        self, mock_user_chars, mock_notify_admins
    ):
        """Buy pending mismatch alert should include exact missing and surplus quantities."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.core.cache import cache
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import (
            ESIContract,
            ESIContractItem,
            MaterialExchangeBuyOrderItem,
        )

        buyer_char_id = 999999999
        mock_user_chars.return_value = [buyer_char_id]

        MaterialExchangeBuyOrderItem.objects.create(
            order=self.buy_order,
            type_id=37,
            type_name="Isogen",
            quantity=4,
            unit_price=7,
            total_price=28,
            stock_available_at_creation=1000,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=self.buy_order,
            type_id=35,
            type_name="Pyerite",
            quantity=10,
            unit_price=8,
            total_price=80,
            stock_available_at_creation=1000,
        )

        pending_contract = ESIContract.objects.create(
            contract_id=227079048,
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
            contract=pending_contract,
            record_id=5,
            type_id=34,
            quantity=500,
            is_included=True,
        )
        ESIContractItem.objects.create(
            contract=pending_contract,
            record_id=6,
            type_id=37,
            quantity=7,
            is_included=True,
        )
        ESIContractItem.objects.create(
            contract=pending_contract,
            record_id=7,
            type_id=35,
            quantity=3,
            is_included=True,
        )

        old_created_at = timezone.now() - timedelta(hours=25)
        MaterialExchangeBuyOrder.objects.filter(pk=self.buy_order.pk).update(
            created_at=old_created_at
        )

        cache.delete(
            f"material_exchange:buy_order:{self.buy_order.id}:contract_reminder"
        )

        validate_material_exchange_buy_orders()

        self.buy_order.refresh_from_db()
        self.assertIn("Missing:", self.buy_order.notes)
        self.assertIn("- 7 Pyerite", self.buy_order.notes)
        self.assertIn("Surplus:", self.buy_order.notes)
        self.assertIn("- 3 Isogen", self.buy_order.notes)

        self.assertTrue(mock_notify_admins.called)
        admin_message = mock_notify_admins.call_args[0][2]
        self.assertIn("Missing:", admin_message)
        self.assertIn("- 7 Pyerite", admin_message)
        self.assertIn("Surplus:", admin_message)
        self.assertIn("- 3 Isogen", admin_message)


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
            title=self.sell_order.order_reference,
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
            title=self.sell_order.order_reference,
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
            structure_id=60003760,
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


class NotificationDeduplicationTest(TestCase):
    """Ensure periodic material exchange cycle does not re-send identical alerts."""

    def setUp(self):
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60003760,
            structure_name="Test Structure",
            is_active=True,
        )
        self.seller = User.objects.create_user(username="dedupe_seller")
        self.buyer = User.objects.create_user(username="dedupe_buyer")

    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_awaiting_buy_notification_throttled_across_cycles(self, mock_notify_user):
        """Awaiting-validation buy order ping should be sent once per throttle window."""
        # Django
        from django.core.cache import cache

        order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
            status=MaterialExchangeBuyOrder.Status.AWAITING_VALIDATION,
            order_reference="INDY-AWAIT-1",
        )

        cache.delete(f"material_exchange:buy_order:{order.id}:awaiting_validation_ping")

        validate_material_exchange_buy_orders()
        validate_material_exchange_buy_orders()

        self.assertEqual(mock_notify_user.call_count, 1)

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_sell_anomaly_notifications_not_repeated_for_unchanged_state(
        self,
        mock_notify_user,
        mock_notify_multi,
        mock_get_character_ids,
    ):
        """Same sell-order anomaly should not notify user/admin every cycle."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract

        seller_char_id = 987654321
        mock_get_character_ids.return_value = [seller_char_id]

        sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-ANOM-1",
        )

        MaterialExchangeSellOrderItem.objects.create(
            order=sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=100,
            unit_price=10,
            total_price=1000,
        )

        ESIContract.objects.create(
            contract_id=555001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=70000001,
            end_location_id=70000001,
            status="outstanding",
            price=sell_order.total_price,
            title=sell_order.order_reference,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )

        validate_material_exchange_sell_orders()
        validate_material_exchange_sell_orders()

        self.assertEqual(mock_notify_user.call_count, 1)
        self.assertEqual(mock_notify_multi.call_count, 1)

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_multi")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_anomaly_contract_finished_is_force_validated(
        self,
        mock_notify_user,
        mock_notify_multi,
        mock_get_character_ids,
    ):
        """An anomalous contract accepted in-game should move sell order to validated."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract

        seller_char_id = 222333444
        mock_get_character_ids.return_value = [seller_char_id]

        sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.ANOMALY,
            order_reference="INDY-ANOM-FINISHED-1",
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=100,
            unit_price=10,
            total_price=1000,
        )

        ESIContract.objects.create(
            contract_id=777001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=70000001,
            end_location_id=70000001,
            status="finished",
            price=sell_order.total_price,
            title=sell_order.order_reference,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )

        validate_material_exchange_sell_orders()

        sell_order.refresh_from_db()
        self.assertEqual(sell_order.status, MaterialExchangeSellOrder.Status.VALIDATED)
        self.assertEqual(sell_order.esi_contract_id, 777001)
        self.assertIn("accepted in-game despite anomaly", sell_order.notes)
        self.assertTrue(mock_notify_user.called)
        self.assertTrue(mock_notify_multi.called)

    @patch("indy_hub.tasks.material_exchange_contracts._get_user_character_ids")
    @patch("indy_hub.tasks.material_exchange_contracts.notify_user")
    def test_anomaly_contract_rejected_stays_open_for_redo(
        self,
        mock_notify_user,
        mock_get_character_ids,
    ):
        """Rejected in-game anomaly contract should not cancel order and must allow later recovery."""
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        # AA Example App
        from indy_hub.models import ESIContract, ESIContractItem

        seller_char_id = 555666777
        mock_get_character_ids.return_value = [seller_char_id]

        sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.ANOMALY,
            order_reference="INDY-ANOM-REJECTED-1",
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=100,
            unit_price=10,
            total_price=1000,
        )

        ESIContract.objects.create(
            contract_id=888001,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=70000001,
            end_location_id=70000001,
            status="rejected",
            price=sell_order.total_price,
            title=sell_order.order_reference,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )

        validate_material_exchange_sell_orders()

        sell_order.refresh_from_db()
        self.assertEqual(
            sell_order.status,
            MaterialExchangeSellOrder.Status.ANOMALY_REJECTED,
        )
        self.assertIn("remains open", sell_order.notes)

        valid_contract = ESIContract.objects.create(
            contract_id=888002,
            corporation_id=self.config.corporation_id,
            contract_type="item_exchange",
            issuer_id=seller_char_id,
            issuer_corporation_id=self.config.corporation_id,
            assignee_id=self.config.corporation_id,
            start_location_id=self.config.structure_id,
            end_location_id=self.config.structure_id,
            status="outstanding",
            price=sell_order.total_price,
            title=sell_order.order_reference,
            date_issued=timezone.now(),
            date_expired=timezone.now() + timedelta(days=30),
        )
        ESIContractItem.objects.create(
            contract=valid_contract,
            record_id=9001,
            type_id=34,
            quantity=100,
            is_included=True,
        )

        validate_material_exchange_sell_orders()

        sell_order.refresh_from_db()
        self.assertEqual(sell_order.status, MaterialExchangeSellOrder.Status.VALIDATED)
        self.assertEqual(sell_order.esi_contract_id, valid_contract.contract_id)
        self.assertTrue(mock_notify_user.called)


if __name__ == "__main__":
    # Standard Library
    import unittest

    unittest.main()
