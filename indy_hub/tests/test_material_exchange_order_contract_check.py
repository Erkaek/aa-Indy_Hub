# Django
from django.contrib.auth.models import User
from django.test import TestCase

# AA Example App
from indy_hub.models import (
    MaterialExchangeAcceptedLocation,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
)
from indy_hub.views.material_exchange_orders import _build_contract_check_payload


class MaterialExchangeOrderContractCheckTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(username="contract-check-seller")
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456789,
            structure_id=60003760,
            structure_name="Primary Structure",
            hangar_division=1,
            is_active=True,
        )
        MaterialExchangeAcceptedLocation.objects.create(
            config=self.config,
            structure_id=60003760,
            structure_name="Primary Structure",
            hangar_division=1,
            sort_order=0,
        )
        MaterialExchangeAcceptedLocation.objects.create(
            config=self.config,
            structure_id=60003761,
            structure_name="Secondary Structure",
            hangar_division=2,
            sort_order=1,
        )

        self.order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-TEST-REF",
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=self.order,
            type_id=34,
            type_name="Tritanium",
            quantity=100,
            unit_price=5,
            total_price=500,
        )

    def test_contract_check_accepts_any_configured_location_name(self):
        payload = _build_contract_check_payload(
            order=self.order,
            order_type="sell",
            raw_text=(
                "Contract Type\tItem Exchange\n"
                "Description\tINDY-TEST-REF\n"
                "Availability\tTest Corporation\n"
                "Location\tSecondary Structure\n"
                "I will receive\t500 ISK\n"
                "Items For Sale\tTritanium x 100\n"
            ),
            recipient_name="Test Corporation",
            location_name="Primary Structure, Secondary Structure",
            accepted_location_names=["Primary Structure", "Secondary Structure"],
        )

        location_check = next(
            check for check in payload["checks"] if check["key"] == "location"
        )
        self.assertTrue(location_check["passed"])

    def test_contract_check_rejects_unknown_location(self):
        payload = _build_contract_check_payload(
            order=self.order,
            order_type="sell",
            raw_text=(
                "Contract Type\tItem Exchange\n"
                "Description\tINDY-TEST-REF\n"
                "Availability\tTest Corporation\n"
                "Location\tWrong Structure\n"
                "I will receive\t500 ISK\n"
                "Items For Sale\tTritanium x 100\n"
            ),
            recipient_name="Test Corporation",
            location_name="Primary Structure, Secondary Structure",
            accepted_location_names=["Primary Structure", "Secondary Structure"],
        )

        location_check = next(
            check for check in payload["checks"] if check["key"] == "location"
        )
        self.assertFalse(location_check["passed"])
