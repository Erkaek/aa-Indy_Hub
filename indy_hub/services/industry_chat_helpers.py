"""Chat utility helpers for blueprint copy request flows."""

from __future__ import annotations

# Django
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import BlueprintCopyChat, BlueprintCopyMessage

__all__ = [
    "build_bp_chat_history_payload",
    "chat_has_unread",
    "chat_preview_messages",
    "close_request_chats",
    "ensure_offer_chat",
    "execute_bp_chat_decision",
    "resolve_chat_viewer_role",
]


def ensure_offer_chat(offer) -> BlueprintCopyChat:
    chat = offer.ensure_chat()
    chat.reopen()
    return chat


def chat_has_unread(chat: BlueprintCopyChat, role: str) -> bool:
    try:
        return chat.has_unread_for(role)
    except AttributeError:
        return False


def chat_preview_messages(chat: BlueprintCopyChat, *, limit: int = 3) -> list[dict]:
    if not chat:
        return []

    role_labels = {
        BlueprintCopyMessage.SenderRole.BUYER: _("Buyer"),
        BlueprintCopyMessage.SenderRole.SELLER: _("Builder"),
        BlueprintCopyMessage.SenderRole.SYSTEM: _("System"),
    }

    preview = []
    for message in chat.messages.order_by("-created_at", "-id")[:limit]:
        created_local = timezone.localtime(message.created_at)
        preview.append(
            {
                "role": message.sender_role,
                "role_label": role_labels.get(
                    message.sender_role, message.sender_role.title()
                ),
                "content": message.content,
                "created_display": created_local.strftime("%Y-%m-%d %H:%M"),
            }
        )

    return preview


def resolve_chat_viewer_role(
    chat: BlueprintCopyChat,
    user,
    *,
    base_role: str | None,
    override: str | None = None,
) -> str | None:
    viewer_role = base_role
    if not override or not base_role:
        return viewer_role

    candidate = str(override).strip().lower()
    if candidate not in {"buyer", "seller"}:
        return viewer_role

    if candidate == base_role:
        return viewer_role

    if chat.buyer_id and chat.seller_id and chat.buyer_id == chat.seller_id == user.id:
        return candidate

    return viewer_role


def close_request_chats(
    req,
    reason: str,
    *,
    exclude_offer_id: int | None = None,
) -> None:
    chats = BlueprintCopyChat.objects.filter(request=req, is_open=True)
    if exclude_offer_id is not None:
        chats = chats.exclude(offer_id=exclude_offer_id)
    for chat in chats:
        chat.close(reason=reason)


def build_bp_chat_history_payload(
    *,
    chat,
    user,
    requested_role,
    resolve_chat_viewer_role_fn,
    classify_bp_chat_message_fn,
    format_isk_amount_fn,
    get_type_name_fn,
) -> dict:
    base_role = chat.role_for(user)
    viewer_role = resolve_chat_viewer_role_fn(
        chat,
        user,
        base_role=base_role,
        override=requested_role,
    )
    if viewer_role not in {"buyer", "seller"}:
        return {"ok": False, "status": 403, "data": {"error": _("Unauthorized")}}

    role_labels = {
        "buyer": _("Buyer"),
        "seller": _("Builder"),
        "system": _("System"),
    }
    messages_payload = []
    for msg in chat.messages.all():
        created_local = timezone.localtime(msg.created_at)
        message_kind = classify_bp_chat_message_fn(msg)
        messages_payload.append(
            {
                "id": msg.id,
                "role": msg.sender_role,
                "kind": message_kind,
                "kind_label": _("Negotiation") if message_kind == "proposal" else "",
                "content": msg.content,
                "created_at": created_local.isoformat(),
                "created_display": created_local.strftime("%Y-%m-%d %H:%M"),
            }
        )

    other_role = "seller" if viewer_role == "buyer" else "buyer"

    decision_payload = None
    offer = getattr(chat, "offer", None)
    if offer and chat.is_open and offer.status == "conditional":
        accepted_by_buyer = offer.accepted_by_buyer
        accepted_by_seller = offer.accepted_by_seller
        proposed_amount = offer.proposed_amount
        proposed_amount_display = format_isk_amount_fn(proposed_amount)

        if viewer_role == "buyer":
            viewer_can_accept = bool(proposed_amount) and not accepted_by_buyer
            viewer_can_propose = True
            accept_label = _("Accept amount")
            proposal_label = (
                _("Counter-propose")
                if proposed_amount is not None
                else _("Propose amount")
            )
            if proposed_amount is None:
                status_label = _("Waiting for first price")
                hint_label = _(
                    "The builder has not shared a price yet. Once they do, you can accept it or counter."
                )
                status_tone = "warning"
                state = "awaiting_seller_proposal"
            elif accepted_by_buyer and not accepted_by_seller:
                status_label = _(
                    "You proposed %(amount)s ISK. Waiting for the builder to confirm or counter."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "Your price is on the table. The builder can validate it or send back another amount."
                )
                status_tone = "warning"
                state = "waiting_on_seller"
            elif not accepted_by_buyer and accepted_by_seller:
                status_label = _(
                    "Builder proposed %(amount)s ISK. Accept it or send a counter-proposal."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "If the price works for you, accept it. Otherwise send back the amount you want."
                )
                status_tone = "info"
                state = "waiting_on_you"
            else:
                status_label = _(
                    "Current proposal: %(amount)s ISK. Accept it or send a counter-proposal."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "Keep the conversation moving by validating this amount or sending a cleaner counter-offer."
                )
                status_tone = "info"
                state = "pending"
        else:
            viewer_can_accept = bool(proposed_amount) and not accepted_by_seller
            viewer_can_propose = True
            accept_label = _("Confirm amount")
            proposal_label = (
                _("Counter-propose")
                if proposed_amount is not None
                else _("Propose amount")
            )
            if proposed_amount is None:
                status_label = _("Set your opening price")
                hint_label = _(
                    "Start the discussion with a clear amount. The buyer will be able to accept it or counter."
                )
                status_tone = "info"
                state = "awaiting_seller_proposal"
            elif accepted_by_buyer and not accepted_by_seller:
                status_label = _(
                    "Buyer accepted %(amount)s ISK. Confirm it or counter-propose."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "You can lock this amount now or keep the negotiation open with a new proposal."
                )
                status_tone = "warning"
                state = "waiting_on_you"
            elif accepted_by_seller and not accepted_by_buyer:
                status_label = _(
                    "You proposed %(amount)s ISK. Waiting for the buyer to confirm or counter."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "The buyer has your price. They can approve it directly or answer with another amount."
                )
                status_tone = "info"
                state = "waiting_on_buyer"
            else:
                status_label = _(
                    "Current proposal: %(amount)s ISK. Confirm it or send a counter-proposal."
                ) % {"amount": proposed_amount_display}
                hint_label = _(
                    "Confirm this amount if it works for you, or keep negotiating with a new price."
                )
                status_tone = "info"
                state = "pending"

        decision_payload = {
            "url": reverse("indy_hub:bp_chat_decide", args=[chat.id]),
            "accepted_by_buyer": accepted_by_buyer,
            "accepted_by_seller": accepted_by_seller,
            "viewer_can_accept": viewer_can_accept,
            "viewer_can_propose": viewer_can_propose,
            "viewer_can_reject": True,
            "accept_label": accept_label,
            "reject_label": _("Decline negotiation"),
            "proposal_label": proposal_label,
            "proposal_placeholder": _("Enter amount in ISK"),
            "current_amount": (
                str(proposed_amount) if proposed_amount is not None else ""
            ),
            "current_amount_display": proposed_amount_display,
            "status_label": status_label,
            "hint_label": hint_label,
            "status_tone": status_tone,
            "state": state,
            "pending_label": _("Updating proposal..."),
        }

    data = {
        "chat": {
            "id": chat.id,
            "is_open": chat.is_open,
            "closed_reason": chat.closed_reason,
            "viewer_role": viewer_role,
            "other_role": other_role,
            "labels": role_labels,
            "type_id": chat.request.type_id,
            "type_name": get_type_name_fn(chat.request.type_id),
            "material_efficiency": chat.request.material_efficiency,
            "time_efficiency": chat.request.time_efficiency,
            "runs_requested": chat.request.runs_requested,
            "copies_requested": chat.request.copies_requested,
            "can_send": chat.is_open and viewer_role in {"buyer", "seller"},
            "decision": decision_payload,
        },
        "messages": messages_payload,
    }

    return {"ok": True, "viewer_role": viewer_role, "data": data}


def execute_bp_chat_decision(
    *,
    chat,
    request_user,
    viewer_role: str,
    decision: str,
    payload,
    normalize_offer_amount_fn,
    record_offer_proposal_fn,
    mark_offer_buyer_accept_fn,
    mark_offer_seller_accept_fn,
    format_isk_amount_fn,
    notify_user_fn,
    get_type_name_fn,
    build_site_url_fn,
    finalize_all_rejected_fn,
) -> dict[str, object]:
    offer = chat.offer
    req = chat.request

    if decision == "propose":
        proposed_amount = normalize_offer_amount_fn(payload.get("amount"))
        if proposed_amount is None:
            return {
                "status": 400,
                "data": {"error": _("Enter a valid proposal amount in ISK.")},
                "flash": ("error", _("Enter a valid proposal amount in ISK.")),
            }

        record_offer_proposal_fn(
            offer,
            proposer_role=viewer_role,
            amount=proposed_amount,
            sender=request_user,
        )

        recipient = chat.seller if viewer_role == "buyer" else chat.buyer
        if recipient:
            notify_user_fn(
                recipient,
                _("New amount proposal"),
                _(
                    "%(actor)s proposed %(amount)s ISK for %(type)s (ME%(me)s, TE%(te)s)."
                )
                % {
                    "actor": request_user.username,
                    "amount": format_isk_amount_fn(proposed_amount),
                    "type": get_type_name_fn(req.type_id),
                    "me": req.material_efficiency,
                    "te": req.time_efficiency,
                },
                "info",
                link=build_site_url_fn(
                    reverse(
                        "indy_hub:bp_copy_fulfill_requests"
                        if viewer_role == "buyer"
                        else "indy_hub:bp_copy_my_requests"
                    )
                ),
                link_label=_("Open details"),
            )

        return {
            "status": 200,
            "data": {
                "status": "pending",
                "proposed_amount": str(offer.proposed_amount),
                "accepted_by_buyer": offer.accepted_by_buyer,
                "accepted_by_seller": offer.accepted_by_seller,
            },
            "flash": ("success", _("Negotiation proposal sent.")),
        }

    if decision == "accept":
        if offer.proposed_amount is None:
            return {
                "status": 400,
                "data": {"error": _("No amount is available to confirm yet.")},
                "flash": ("error", _("No amount is available to confirm yet.")),
            }
        if viewer_role == "buyer":
            if offer.accepted_by_buyer and not offer.accepted_by_seller:
                return {
                    "status": 200,
                    "data": {
                        "status": "pending",
                        "accepted_by_buyer": True,
                        "accepted_by_seller": False,
                    },
                    "flash": (
                        "info",
                        _("You already accepted this amount. Waiting for the builder."),
                    ),
                }
            finalized = mark_offer_buyer_accept_fn(offer)
            if finalized:
                return {
                    "status": 200,
                    "data": {"status": "accepted"},
                    "flash": ("success", _("Amount accepted. Delivery can proceed.")),
                }

            fulfill_queue_url = build_site_url_fn(
                reverse("indy_hub:bp_copy_fulfill_requests")
            )
            notify_user_fn(
                chat.seller,
                _("Conditional offer accepted"),
                _(
                    "%(buyer)s accepted %(amount)s ISK for %(type)s (ME%(me)s, TE%(te)s). Confirm it or counter-propose."
                )
                % {
                    "buyer": req.requested_by.username,
                    "amount": format_isk_amount_fn(offer.proposed_amount),
                    "type": get_type_name_fn(req.type_id),
                    "me": req.material_efficiency,
                    "te": req.time_efficiency,
                },
                "info",
                link=fulfill_queue_url,
                link_label=_("Open fulfill queue"),
            )
            return {
                "status": 200,
                "data": {
                    "status": "pending",
                    "accepted_by_buyer": True,
                    "accepted_by_seller": offer.accepted_by_seller,
                },
                "flash": ("success", _("Amount accepted. Waiting for the builder.")),
            }

        if offer.accepted_by_seller and not offer.accepted_by_buyer:
            return {
                "status": 200,
                "data": {
                    "status": "pending",
                    "accepted_by_buyer": False,
                    "accepted_by_seller": True,
                },
                "flash": (
                    "info",
                    _("You already confirmed this amount. Waiting for the buyer."),
                ),
            }

        finalized = mark_offer_seller_accept_fn(offer)
        if finalized:
            return {
                "status": 200,
                "data": {"status": "accepted"},
                "flash": (
                    "success",
                    _("Terms confirmed. The request is ready for delivery."),
                ),
            }

        buyer_requests_url = build_site_url_fn(reverse("indy_hub:bp_copy_my_requests"))
        notify_user_fn(
            chat.buyer,
            _("Builder confirmed your terms"),
            _(
                "%(builder)s confirmed %(amount)s ISK for %(type)s (ME%(me)s, TE%(te)s). Accept it or counter-propose."
            )
            % {
                "builder": offer.owner.username,
                "amount": format_isk_amount_fn(offer.proposed_amount),
                "type": get_type_name_fn(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "info",
            link=buyer_requests_url,
            link_label=_("Review your requests"),
        )
        return {
            "status": 200,
            "data": {
                "status": "pending",
                "accepted_by_buyer": offer.accepted_by_buyer,
                "accepted_by_seller": True,
            },
            "flash": ("success", _("Amount confirmed. Waiting for the buyer.")),
        }

    offer.status = "rejected"
    offer.accepted_by_buyer = False
    offer.accepted_by_seller = False
    offer.accepted_at = None
    offer.save(
        update_fields=[
            "status",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
    )

    chat.close(reason=BlueprintCopyChat.CloseReason.OFFER_REJECTED)

    recipient = chat.seller if viewer_role == "buyer" else chat.buyer
    if recipient:
        notify_user_fn(
            recipient,
            _("Conditional offer declined"),
            _(
                "%(actor)s declined the conditional offer for %(type)s (ME%(me)s, TE%(te)s)."
            )
            % {
                "actor": request_user.username,
                "type": get_type_name_fn(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "warning",
            link=build_site_url_fn(
                reverse(
                    "indy_hub:bp_copy_fulfill_requests"
                    if viewer_role == "buyer"
                    else "indy_hub:bp_copy_my_requests"
                )
            ),
            link_label=_("Open details"),
        )

    if viewer_role == "seller" and finalize_all_rejected_fn(req):
        return {
            "status": 200,
            "data": {"status": "rejected", "request_closed": True},
            "flash": ("success", _("Negotiation declined and request closed.")),
        }

    if not req.offers.exclude(id=offer.id).filter(status="accepted").exists():
        reset_fields: list[str] = []
        if req.delivered:
            req.delivered = False
            req.delivered_at = None
            reset_fields.extend(["delivered", "delivered_at"])
        if req.fulfilled:
            req.fulfilled = False
            req.fulfilled_at = None
            reset_fields.extend(["fulfilled", "fulfilled_at"])
        if reset_fields:
            req.save(update_fields=list(dict.fromkeys(reset_fields)))

    return {
        "status": 200,
        "data": {"status": "rejected"},
        "flash": ("success", _("Negotiation declined.")),
    }
