# Django
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeSellOrder


class MaterialExchangeRejectSellTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("hub-admin", password="secret123")

        character, _ = EveCharacter.objects.get_or_create(
            character_id=7004001,
            defaults={
                "character_name": "Hub Admin",
                "corporation_id": 2_000_000,
                "corporation_name": "Test Corp",
                "corporation_ticker": "TEST",
            },
        )
        CharacterOwnership.objects.update_or_create(
            user=self.admin,
            character=character,
            defaults={"owner_hash": f"hash-{character.character_id}-{self.admin.id}"},
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.admin)
        profile.main_character = character
        profile.save(update_fields=["main_character"])

        perms = Permission.objects.filter(
            content_type__app_label="indy_hub",
            codename__in=[
                "can_access_indy_hub",
                "can_manage_material_hub",
            ],
        )
        found = {perm.codename for perm in perms}
        missing = {"can_access_indy_hub", "can_manage_material_hub"} - found
        if missing:
            raise AssertionError(f"Missing permissions: {sorted(missing)}")
        self.admin.user_permissions.add(*perms)

        self.seller = User.objects.create_user("seller", password="secret123")

        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=60000001,
            structure_name="Test Structure",
            hangar_division=1,
            sell_markup_percent="0.00",
            sell_markup_base="buy",
            buy_markup_percent="5.00",
            buy_markup_base="buy",
            enforce_jita_price_bounds=False,
            notify_admins_on_sell_anomaly=True,
            is_active=True,
        )

    def test_reject_sell_order_in_anomaly_status(self) -> None:
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.ANOMALY,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("indy_hub:material_exchange_reject_sell", args=[order.id])
        )
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, MaterialExchangeSellOrder.Status.REJECTED)

    def test_reject_sell_order_in_anomaly_rejected_status(self) -> None:
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.seller,
            status=MaterialExchangeSellOrder.Status.ANOMALY_REJECTED,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("indy_hub:material_exchange_reject_sell", args=[order.id])
        )
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, MaterialExchangeSellOrder.Status.REJECTED)
