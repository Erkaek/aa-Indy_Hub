"""Offer action orchestration for blueprint copy negotiation flows."""

from __future__ import annotations

# Standard Library
from dataclasses import dataclass
from decimal import Decimal

# Django
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# AA Example App
from ..models import BlueprintCopyChat, BlueprintCopyMessage, BlueprintCopyOffer

__all__ = [
    "OfferFlowDeps",
    "finalize_offer_with_deps",
    "mark_buyer_accept_with_deps",
    "mark_offer_buyer_accept",
    "mark_offer_seller_accept",
    "mark_seller_accept_with_deps",
    "process_offer_action",
    "process_offer_action_with_deps",
    "record_offer_proposal",
]


@dataclass(frozen=True)
class OfferFlowDeps:
    ensure_offer_chat_fn: object
    close_request_chats_fn: object
    strike_webhooks_fn: object
    format_isk_amount_fn: object
    notify_user_fn: object
    get_type_name_fn: object
    build_site_url_fn: object
    record_offer_proposal_fn: object
    close_offer_chat_if_exists_fn: object
    finalize_all_rejected_fn: object
    messages_api: object


def record_offer_proposal(
    offer,
    *,
    proposer_role: str,
    amount,
    sender,
    note: str = "",
    ensure_offer_chat_fn,
    format_isk_amount_fn,
) -> object:
    previous_amount = offer.proposed_amount
    previous_role = offer.proposed_by_role

    offer.status = "conditional"
    offer.proposed_amount = amount
    offer.proposed_by_role = proposer_role
    offer.proposed_at = timezone.now()
    offer.accepted_by_buyer = proposer_role == offer.ProposalRole.BUYER
    offer.accepted_by_seller = proposer_role == offer.ProposalRole.SELLER
    offer.accepted_at = None
    if note:
        offer.message = note
    offer.save()

    chat = ensure_offer_chat_fn(offer)
    proposal_actor = _("Buyer") if proposer_role == "buyer" else _("Builder")
    proposal_verb = (
        _("counter-proposed") if previous_amount is not None else _("proposed")
    )
    if (
        previous_amount is not None
        and previous_role == proposer_role
        and previous_amount == amount
    ):
        proposal_verb = _("reconfirmed")

    proposal_message = BlueprintCopyMessage(
        chat=chat,
        sender=sender,
        sender_role=BlueprintCopyMessage.SenderRole.SYSTEM,
        content=_("%(actor)s %(verb)s %(amount)s ISK.")
        % {
            "actor": proposal_actor,
            "verb": proposal_verb,
            "amount": format_isk_amount_fn(amount),
        },
    )
    proposal_message.full_clean()
    proposal_message.save()
    chat.register_message(sender_role=proposer_role)

    if note:
        note_message = BlueprintCopyMessage(
            chat=chat,
            sender=sender,
            sender_role=proposer_role,
            content=note,
        )
        note_message.full_clean()
        note_message.save()
        chat.register_message(sender_role=proposer_role)

    return chat


def finalize_conditional_offer(
    offer: BlueprintCopyOffer,
    *,
    ensure_offer_chat_fn,
    close_request_chats_fn,
    strike_webhooks_fn,
    format_isk_amount_fn,
    notify_user_fn,
    get_type_name_fn,
    build_site_url_fn,
) -> None:
    req = offer.request
    if offer.status == "accepted" and req.fulfilled:
        return

    ensure_offer_chat_fn(offer)

    offer.status = "accepted"
    offer.accepted_by_buyer = True
    offer.accepted_by_seller = True
    offer.accepted_at = timezone.now()
    offer.save(
        update_fields=[
            "status",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
    )

    req.fulfilled = True
    req.fulfilled_at = timezone.now()
    req.fulfilled_by = offer.owner
    req.save(update_fields=["fulfilled", "fulfilled_at", "fulfilled_by"])

    close_request_chats_fn(
        req,
        BlueprintCopyChat.CloseReason.OFFER_ACCEPTED,
        exclude_offer_id=offer.id,
    )
    strike_webhooks_fn(None, req, actor=offer.owner)
    BlueprintCopyOffer.objects.filter(request=req).exclude(id=offer.id).delete()

    fulfill_queue_url = build_site_url_fn(reverse("indy_hub:bp_copy_fulfill_requests"))
    buyer_requests_url = build_site_url_fn(reverse("indy_hub:bp_copy_my_requests"))

    notify_user_fn(
        offer.owner,
        _("Blueprint Copy Request - Buyer Accepted"),
        _(
            "%(buyer)s accepted your offer for %(type)s (ME%(me)s, TE%(te)s)%(amount_suffix)s."
        )
        % {
            "buyer": req.requested_by.username,
            "type": get_type_name_fn(req.type_id),
            "me": req.material_efficiency,
            "te": req.time_efficiency,
            "amount_suffix": (
                _(" at %(amount)s ISK")
                % {"amount": format_isk_amount_fn(offer.proposed_amount)}
                if offer.proposed_amount is not None
                else ""
            ),
        },
        "success",
        link=fulfill_queue_url,
        link_label=_("Open fulfill queue"),
    )

    notify_user_fn(
        req.requested_by,
        _("Conditional offer confirmed"),
        _(
            "%(builder)s confirmed your agreement for %(type)s (ME%(me)s, TE%(te)s)%(amount_suffix)s."
        )
        % {
            "builder": offer.owner.username,
            "type": get_type_name_fn(req.type_id),
            "me": req.material_efficiency,
            "te": req.time_efficiency,
            "amount_suffix": (
                _(" at %(amount)s ISK")
                % {"amount": format_isk_amount_fn(offer.proposed_amount)}
                if offer.proposed_amount is not None
                else ""
            ),
        },
        "success",
        link=buyer_requests_url,
        link_label=_("Review your requests"),
    )


def mark_offer_buyer_accept(offer: BlueprintCopyOffer, *, finalize_offer_fn) -> bool:
    if (
        offer.status == "accepted"
        and offer.accepted_by_buyer
        and offer.accepted_by_seller
    ):
        return True

    if not offer.accepted_by_buyer:
        offer.accepted_by_buyer = True
        offer.save(update_fields=["accepted_by_buyer"])

    if offer.accepted_by_seller:
        finalize_offer_fn(offer)
        return True
    return False


def mark_offer_seller_accept(offer: BlueprintCopyOffer, *, finalize_offer_fn) -> bool:
    if (
        offer.status == "accepted"
        and offer.accepted_by_buyer
        and offer.accepted_by_seller
    ):
        return True

    if not offer.accepted_by_seller:
        offer.accepted_by_seller = True
        offer.save(update_fields=["accepted_by_seller"])

    if offer.accepted_by_buyer:
        finalize_offer_fn(offer)
        return True
    return False


def process_offer_action(
    *,
    request_obj,
    req,
    owner,
    action: str | None,
    message: str = "",
    source_scope: str | None = None,
    proposed_amount: Decimal | None = None,
    record_offer_proposal_fn,
    ensure_offer_chat_fn,
    close_offer_chat_if_exists_fn,
    close_request_chats_fn,
    strike_webhooks_fn,
    finalize_all_rejected_fn,
    notify_user_fn,
    format_isk_amount_fn,
    get_type_name_fn,
    messages_api,
) -> bool:
    if not action:
        return False

    normalized_scope = None
    if source_scope is not None:
        candidate = str(source_scope).strip().lower()
        if candidate in {"personal", "corporation"}:
            normalized_scope = candidate

    offer, _created = BlueprintCopyOffer.objects.get_or_create(request=req, owner=owner)
    if normalized_scope:
        offer.source_scope = normalized_scope
    my_requests_url = request_obj.build_absolute_uri(
        reverse("indy_hub:bp_copy_my_requests")
    )

    if action == "accept":
        offer.status = "accepted"
        offer.message = ""
        offer.accepted_by_buyer = True
        offer.accepted_by_seller = True
        offer.accepted_at = timezone.now()
        update_fields = [
            "status",
            "message",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
        if normalized_scope:
            update_fields.append("source_scope")
        offer.save(update_fields=[*update_fields])
        close_offer_chat_if_exists_fn(offer, BlueprintCopyChat.CloseReason.OFFER_ACCEPTED)
        notify_user_fn(
            req.requested_by,
            "Blueprint Copy Request Accepted",
            f"{owner.username} accepted your copy request for {get_type_name_fn(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) for free.",
            "success",
            link=my_requests_url,
            link_label=_("Review your requests"),
        )
        req.fulfilled = True
        req.fulfilled_at = timezone.now()
        req.fulfilled_by = owner
        req.save(update_fields=["fulfilled", "fulfilled_at", "fulfilled_by"])
        close_request_chats_fn(req, BlueprintCopyChat.CloseReason.OFFER_ACCEPTED)
        strike_webhooks_fn(request_obj, req, actor=owner)
        BlueprintCopyOffer.objects.filter(request=req).exclude(owner=owner).delete()
        messages_api.success(request_obj, _("Request accepted and requester notified."))
        return True

    if action == "conditional":
        if proposed_amount is not None:
            if normalized_scope:
                offer.source_scope = normalized_scope
            record_offer_proposal_fn(
                offer,
                proposer_role=BlueprintCopyOffer.ProposalRole.SELLER,
                amount=proposed_amount,
                sender=owner,
                note=message,
            )
        else:
            offer.status = "conditional"
            offer.message = message
            offer.accepted_by_buyer = False
            offer.accepted_by_seller = False
            offer.accepted_at = None
            update_fields = [
                "status",
                "message",
                "accepted_by_buyer",
                "accepted_by_seller",
                "accepted_at",
            ]
            if normalized_scope:
                update_fields.append("source_scope")
            offer.save(update_fields=[*update_fields])
            chat = ensure_offer_chat_fn(offer)
            if message:
                chat_message = BlueprintCopyMessage(
                    chat=chat,
                    sender=owner,
                    sender_role=BlueprintCopyMessage.SenderRole.SELLER,
                    content=message,
                )
                chat_message.full_clean()
                chat_message.save()
                chat.register_message(
                    sender_role=BlueprintCopyMessage.SenderRole.SELLER
                )
        notify_user_fn(
            req.requested_by,
            _("Blueprint Copy Request - Conditional Offer"),
            (
                _(
                    "You received a new amount proposal of %(amount)s ISK for %(type)s (ME%(me)s, TE%(te)s)."
                )
                % {
                    "amount": format_isk_amount_fn(proposed_amount),
                    "type": get_type_name_fn(req.type_id),
                    "me": req.material_efficiency,
                    "te": req.time_efficiency,
                }
                if proposed_amount is not None
                else _(
                    "You received a new conditional offer message for %(type)s (ME%(me)s, TE%(te)s)."
                )
                % {
                    "type": get_type_name_fn(req.type_id),
                    "me": req.material_efficiency,
                    "te": req.time_efficiency,
                }
            )
            % {
                "type": get_type_name_fn(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "info",
            link=my_requests_url,
            link_label=_("Review your requests"),
        )
        if proposed_amount is not None:
            messages_api.success(request_obj, _("Amount proposal sent."))
        elif message:
            messages_api.success(request_obj, _("Conditional offer sent."))
        else:
            messages_api.success(
                request_obj,
                _("Conditional offer started. Continue the discussion in chat."),
            )
        return True

    if action == "reject":
        offer.status = "rejected"
        offer.message = message
        offer.accepted_by_buyer = False
        offer.accepted_by_seller = False
        offer.accepted_at = None
        update_fields = [
            "status",
            "message",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
        if normalized_scope:
            update_fields.append("source_scope")
        offer.save(update_fields=[*update_fields])
        close_offer_chat_if_exists_fn(offer, BlueprintCopyChat.CloseReason.OFFER_REJECTED)
        if finalize_all_rejected_fn(req):
            messages_api.success(
                request_obj,
                _("Offer rejected. Requester notified that no builders are available."),
            )
        else:
            messages_api.success(request_obj, _("Offer rejected."))
        return True

    return False


def finalize_offer_with_deps(offer, *, deps: OfferFlowDeps) -> None:
    finalize_conditional_offer(
        offer,
        ensure_offer_chat_fn=deps.ensure_offer_chat_fn,
        close_request_chats_fn=deps.close_request_chats_fn,
        strike_webhooks_fn=deps.strike_webhooks_fn,
        format_isk_amount_fn=deps.format_isk_amount_fn,
        notify_user_fn=deps.notify_user_fn,
        get_type_name_fn=deps.get_type_name_fn,
        build_site_url_fn=deps.build_site_url_fn,
    )


def mark_buyer_accept_with_deps(offer, *, deps: OfferFlowDeps) -> bool:
    return mark_offer_buyer_accept(
        offer,
        finalize_offer_fn=lambda value: finalize_offer_with_deps(value, deps=deps),
    )


def mark_seller_accept_with_deps(offer, *, deps: OfferFlowDeps) -> bool:
    return mark_offer_seller_accept(
        offer,
        finalize_offer_fn=lambda value: finalize_offer_with_deps(value, deps=deps),
    )


def process_offer_action_with_deps(
    *,
    request_obj,
    req,
    owner,
    action,
    message,
    source_scope,
    proposed_amount,
    deps: OfferFlowDeps,
) -> bool:
    return process_offer_action(
        request_obj=request_obj,
        req=req,
        owner=owner,
        action=action,
        message=message,
        source_scope=source_scope,
        proposed_amount=proposed_amount,
        record_offer_proposal_fn=deps.record_offer_proposal_fn,
        ensure_offer_chat_fn=deps.ensure_offer_chat_fn,
        close_offer_chat_if_exists_fn=deps.close_offer_chat_if_exists_fn,
        close_request_chats_fn=deps.close_request_chats_fn,
        strike_webhooks_fn=deps.strike_webhooks_fn,
        finalize_all_rejected_fn=deps.finalize_all_rejected_fn,
        notify_user_fn=deps.notify_user_fn,
        format_isk_amount_fn=deps.format_isk_amount_fn,
        get_type_name_fn=deps.get_type_name_fn,
        messages_api=deps.messages_api,
    )