# Standard Library
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Django
from django.test import SimpleTestCase
from django.utils import timezone

# AA Example App
from indy_hub.services.industry_chat_helpers import (
    build_bp_chat_history_payload,
    execute_bp_chat_decision,
)
from indy_hub.services.industry_offer_actions import (
    OfferFlowDeps,
    mark_buyer_accept_with_deps,
)


class _DummyMessages:
    def __init__(self, messages):
        self._messages = list(messages)

    def all(self):
        return list(self._messages)


class IndustryChatPayloadServiceTests(SimpleTestCase):
    def test_history_payload_rejects_unauthorized_viewer_role(self):
        request_user = SimpleNamespace(id=10)
        chat = SimpleNamespace(
            id=42,
            is_open=True,
            closed_reason="",
            request=SimpleNamespace(
                type_id=603,
                material_efficiency=8,
                time_efficiency=12,
                runs_requested=1,
                copies_requested=1,
            ),
            messages=_DummyMessages([]),
            role_for=lambda _user: None,
            offer=None,
        )

        payload = build_bp_chat_history_payload(
            chat=chat,
            user=request_user,
            requested_role="buyer",
            resolve_chat_viewer_role_fn=lambda *_args, **_kwargs: None,
            classify_bp_chat_message_fn=lambda _msg: "message",
            format_isk_amount_fn=lambda amount: str(amount),
            get_type_name_fn=lambda _type_id: "Type",
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], 403)

    def test_history_payload_builds_conditional_decision_for_buyer(self):
        now = timezone.now()
        request_user = SimpleNamespace(id=10)
        message = SimpleNamespace(
            id=1,
            sender_role="buyer",
            content="hello",
            created_at=now - timedelta(minutes=1),
        )
        offer = SimpleNamespace(
            status="conditional",
            accepted_by_buyer=False,
            accepted_by_seller=True,
            proposed_amount=123,
        )
        chat = SimpleNamespace(
            id=5,
            is_open=True,
            closed_reason="",
            buyer_id=10,
            seller_id=20,
            request=SimpleNamespace(
                type_id=603,
                material_efficiency=8,
                time_efficiency=12,
                runs_requested=2,
                copies_requested=1,
            ),
            messages=_DummyMessages([message]),
            role_for=lambda _user: "buyer",
            offer=offer,
        )

        payload = build_bp_chat_history_payload(
            chat=chat,
            user=request_user,
            requested_role="buyer",
            resolve_chat_viewer_role_fn=lambda *_args, **_kwargs: "buyer",
            classify_bp_chat_message_fn=lambda _msg: "proposal",
            format_isk_amount_fn=lambda amount: f"{amount}",
            get_type_name_fn=lambda _type_id: "Widget",
        )

        self.assertTrue(payload["ok"])
        decision = payload["data"]["chat"]["decision"]
        self.assertEqual(decision["state"], "waiting_on_you")
        self.assertTrue(decision["viewer_can_accept"])
        self.assertEqual(payload["data"]["messages"][0]["kind"], "proposal")


class IndustryChatDecisionServiceTests(SimpleTestCase):
    def _base_chat(self):
        buyer = SimpleNamespace(id=10, username="buyer")
        seller = SimpleNamespace(id=20, username="seller")
        request = SimpleNamespace(
            type_id=603,
            material_efficiency=8,
            time_efficiency=12,
            requested_by=buyer,
            offers=Mock(),
            delivered=False,
            fulfilled=False,
        )
        offer = SimpleNamespace(
            proposed_amount=None,
            accepted_by_buyer=False,
            accepted_by_seller=False,
            owner=seller,
            status="conditional",
        )
        return SimpleNamespace(offer=offer, request=request, buyer=buyer, seller=seller)

    def test_propose_invalid_amount_returns_error(self):
        chat = self._base_chat()
        result = execute_bp_chat_decision(
            chat=chat,
            request_user=chat.buyer,
            viewer_role="buyer",
            decision="propose",
            payload={"amount": "bad"},
            normalize_offer_amount_fn=lambda _value: None,
            record_offer_proposal_fn=lambda **_kwargs: None,
            mark_offer_buyer_accept_fn=lambda _offer: False,
            mark_offer_seller_accept_fn=lambda _offer: False,
            format_isk_amount_fn=lambda amount: str(amount),
            notify_user_fn=lambda *args, **kwargs: None,
            get_type_name_fn=lambda _type_id: "Widget",
            build_site_url_fn=lambda path: path,
            finalize_all_rejected_fn=lambda _req: False,
        )

        self.assertEqual(result["status"], 400)
        self.assertIn("error", result["data"])

    def test_propose_valid_amount_returns_pending_and_notifies(self):
        chat = self._base_chat()
        notifications = []

        def record_offer(_offer, **kwargs):
            chat.offer.proposed_amount = kwargs["amount"]
            chat.offer.accepted_by_buyer = kwargs["proposer_role"] == "buyer"
            chat.offer.accepted_by_seller = kwargs["proposer_role"] == "seller"

        result = execute_bp_chat_decision(
            chat=chat,
            request_user=chat.buyer,
            viewer_role="buyer",
            decision="propose",
            payload={"amount": "123"},
            normalize_offer_amount_fn=lambda _value: 123,
            record_offer_proposal_fn=record_offer,
            mark_offer_buyer_accept_fn=lambda _offer: False,
            mark_offer_seller_accept_fn=lambda _offer: False,
            format_isk_amount_fn=lambda amount: str(amount),
            notify_user_fn=lambda *args, **kwargs: notifications.append((args, kwargs)),
            get_type_name_fn=lambda _type_id: "Widget",
            build_site_url_fn=lambda path: path,
            finalize_all_rejected_fn=lambda _req: False,
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["data"]["status"], "pending")
        self.assertEqual(result["data"]["proposed_amount"], "123")
        self.assertEqual(len(notifications), 1)


class IndustryOfferFlowFacadeTests(SimpleTestCase):
    def test_mark_buyer_accept_delegates_to_service(self):
        deps = OfferFlowDeps(
            ensure_offer_chat_fn=Mock(),
            close_request_chats_fn=Mock(),
            strike_webhooks_fn=Mock(),
            format_isk_amount_fn=Mock(),
            notify_user_fn=Mock(),
            get_type_name_fn=Mock(),
            build_site_url_fn=Mock(),
            record_offer_proposal_fn=Mock(),
            close_offer_chat_if_exists_fn=Mock(),
            finalize_all_rejected_fn=Mock(),
            messages_api=Mock(),
        )
        offer = SimpleNamespace()

        with patch(
            "indy_hub.services.industry_offer_actions.mark_offer_buyer_accept",
            return_value=True,
        ) as mocked:
            result = mark_buyer_accept_with_deps(offer, deps=deps)

        self.assertTrue(result)
        mocked.assert_called_once()
