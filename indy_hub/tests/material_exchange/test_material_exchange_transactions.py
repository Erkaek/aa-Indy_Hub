"""Regression tests for Material Exchange transaction snapshots and stats."""

# Standard Library
from decimal import Decimal

# Django
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

# AA Example App
from indy_hub.models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
)
from indy_hub.tasks.material_exchange_contracts import _log_sell_order_transactions
from indy_hub.utils.material_exchange_transactions import (
    upsert_material_exchange_transaction,
)
from indy_hub.views.material_exchange import (
    _complete_buy_order,
    material_exchange_stats_history,
    material_exchange_transactions,
)


class MaterialExchangeTransactionRegressionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60003760,
            structure_name="Test Structure",
            is_active=True,
        )
        self.seller = User.objects.create_user(username="tx_seller")
        self.buyer = User.objects.create_user(username="tx_buyer")
        self.admin = User.objects.create_user(username="tx_admin")
        self.admin.is_staff = True
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_staff", "is_superuser"])

    def test_complete_buy_order_aggregates_multi_item_transaction_once(self):
        MaterialExchangeStock.objects.create(
            config=self.config,
            type_id=34,
            type_name="Tritanium",
            quantity=2000,
        )
        MaterialExchangeStock.objects.create(
            config=self.config,
            type_id=35,
            type_name="Pyerite",
            quantity=1000,
        )
        order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
            status=MaterialExchangeBuyOrder.Status.VALIDATED,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=order,
            type_id=34,
            type_name="Tritanium",
            quantity=1000,
            unit_price=Decimal("5.00"),
            total_price=Decimal("5000.00"),
            stock_available_at_creation=2000,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=order,
            type_id=35,
            type_name="Pyerite",
            quantity=500,
            unit_price=Decimal("8.00"),
            total_price=Decimal("4000.00"),
            stock_available_at_creation=1000,
        )

        _complete_buy_order(order)
        order.refresh_from_db()
        tx = order.transaction

        self.assertEqual(order.status, MaterialExchangeBuyOrder.Status.COMPLETED)
        self.assertEqual(MaterialExchangeTransaction.objects.count(), 1)
        self.assertEqual(tx.total_price, Decimal("9000.00"))
        self.assertEqual(tx.quantity, 1500)
        self.assertEqual(tx.type_name, "Tritanium + 1 more item")

        tritanium_stock = self.config.stock_items.get(type_id=34)
        pyerite_stock = self.config.stock_items.get(type_id=35)
        self.assertEqual(tritanium_stock.quantity, 1000)
        self.assertEqual(pyerite_stock.quantity, 500)

        _complete_buy_order(order)
        tritanium_stock.refresh_from_db()
        pyerite_stock.refresh_from_db()

        self.assertEqual(MaterialExchangeTransaction.objects.count(), 1)
        self.assertEqual(tritanium_stock.quantity, 1000)
        self.assertEqual(pyerite_stock.quantity, 500)

    def test_log_sell_order_transactions_aggregates_multi_item_transaction_once(self):
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.COMPLETED,
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=order,
            type_id=36,
            type_name="Mexallon",
            quantity=300,
            unit_price=Decimal("60.00"),
            total_price=Decimal("18000.00"),
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=order,
            type_id=37,
            type_name="Nocxium",
            quantity=100,
            unit_price=Decimal("800.00"),
            total_price=Decimal("80000.00"),
        )

        _log_sell_order_transactions(order)
        tx = order.transaction

        self.assertEqual(MaterialExchangeTransaction.objects.count(), 1)
        self.assertEqual(tx.total_price, Decimal("98000.00"))
        self.assertEqual(tx.quantity, 400)
        self.assertEqual(tx.type_name, "Mexallon + 1 more item")
        self.assertEqual(self.config.stock_items.get(type_id=36).quantity, 300)
        self.assertEqual(self.config.stock_items.get(type_id=37).quantity, 100)

        _log_sell_order_transactions(order)

        self.assertEqual(MaterialExchangeTransaction.objects.count(), 1)
        self.assertEqual(self.config.stock_items.get(type_id=36).quantity, 300)
        self.assertEqual(self.config.stock_items.get(type_id=37).quantity, 100)

    def test_transaction_pages_use_full_order_totals_for_multi_item_orders(self):
        sell_order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.COMPLETED,
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=sell_order,
            type_id=34,
            type_name="Tritanium",
            quantity=1000,
            unit_price=Decimal("5.00"),
            total_price=Decimal("5000.00"),
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=sell_order,
            type_id=35,
            type_name="Pyerite",
            quantity=500,
            unit_price=Decimal("8.00"),
            total_price=Decimal("4000.00"),
        )

        buy_order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.buyer,
            status=MaterialExchangeBuyOrder.Status.COMPLETED,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=buy_order,
            type_id=36,
            type_name="Mexallon",
            quantity=300,
            unit_price=Decimal("60.00"),
            total_price=Decimal("18000.00"),
            stock_available_at_creation=300,
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=buy_order,
            type_id=37,
            type_name="Nocxium",
            quantity=100,
            unit_price=Decimal("800.00"),
            total_price=Decimal("80000.00"),
            stock_available_at_creation=100,
        )

        sell_tx, _created = upsert_material_exchange_transaction(sell_order)
        buy_tx, _created = upsert_material_exchange_transaction(buy_order)
        current_month = timezone.now().replace(
            day=10, hour=12, minute=0, second=0, microsecond=0
        )
        MaterialExchangeTransaction.objects.filter(pk=sell_tx.pk).update(
            completed_at=current_month
        )
        MaterialExchangeTransaction.objects.filter(pk=buy_tx.pk).update(
            completed_at=current_month
        )

        transactions_request = self.factory.get(
            "/indy_hub/material-exchange/transactions/"
        )
        transactions_request.user = self.admin
        transactions_response = material_exchange_transactions.__wrapped__.__wrapped__(
            transactions_request
        )

        self.assertEqual(transactions_response.status_code, 200)
        transactions_html = transactions_response.content.decode("utf-8")
        self.assertIn("9,000", transactions_html)
        self.assertIn("98,000", transactions_html)

        stats_request = self.factory.get(
            "/indy_hub/material-exchange/transactions/stats-history/"
        )
        stats_request.user = self.admin
        stats_response = material_exchange_stats_history.__wrapped__.__wrapped__(
            stats_request
        )

        self.assertEqual(stats_response.status_code, 200)
        stats_html = stats_response.content.decode("utf-8")
        self.assertIn("9,000", stats_html)
        self.assertIn("98,000", stats_html)
        self.assertIn(">2<", stats_html)
