"""Notification helpers for blueprint copy request flows."""

from __future__ import annotations

# Django
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ..models import (
    Blueprint,
    BlueprintCopyRequest,
    NotificationWebhook,
    NotificationWebhookMessage,
)
from ..notifications import (
    edit_discord_webhook_message,
    notify_user,
    send_discord_webhook_with_message_id,
)
from ..utils.discord_actions import build_action_link
from ..utils.eve import get_type_name
from .industry_blueprint_eligibility import eligible_owner_details_for_request


def build_blueprint_copy_request_notification_content(
    req: BlueprintCopyRequest,
) -> tuple[str, str, str]:
    notification_context = {
        "username": req.requested_by.username,
        "type_name": get_type_name(req.type_id),
        "me": req.material_efficiency,
        "te": req.time_efficiency,
        "runs": req.runs_requested,
        "copies": req.copies_requested,
    }

    notification_title = _("New blueprint copy request")
    notification_body = (
        _(
            "%(username)s requested a copy of %(type_name)s (ME%(me)s, TE%(te)s) — %(runs)s runs, %(copies)s copies requested."
        )
        % notification_context
    )
    corporate_source_line = ""
    corporate_blueprint_qs = (
        Blueprint.objects.filter(
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            type_id=req.type_id,
            material_efficiency=req.material_efficiency,
            time_efficiency=req.time_efficiency,
        )
        .values_list("corporation_name", flat=True)
        .distinct()
    )

    corp_labels: set[str] = set()
    for corp_name in corporate_blueprint_qs:
        label = corp_name.strip() if isinstance(corp_name, str) else ""
        if label:
            corp_labels.add(label)

    if corp_labels:
        formatted_corps = ", ".join(sorted(corp_labels, key=str.lower))
        corporate_source_line = _("Corporate source: %(corporations)s") % {
            "corporations": formatted_corps
        }

    return notification_title, notification_body, corporate_source_line


def strike_discord_webhook_messages_for_request(
    req: BlueprintCopyRequest,
    *,
    edit_webhook_fn=edit_discord_webhook_message,
) -> None:
    webhook_messages = NotificationWebhookMessage.objects.filter(copy_request=req)
    if not webhook_messages.exists():
        return

    notification_title, notification_body, corporate_source_line = (
        build_blueprint_copy_request_notification_content(req)
    )
    provider_body = notification_body
    if corporate_source_line:
        provider_body = f"{provider_body}\n\n{corporate_source_line}"

    strike_title = f"~~{notification_title}~~"
    strike_body = f"~~{provider_body}~~\n\nrequest closed"

    for webhook_message in webhook_messages:
        edit_webhook_fn(
            webhook_message.webhook_url,
            webhook_message.message_id,
            strike_title,
            strike_body,
            level="info",
            link=None,
            embed_title=f"~~📘 {notification_title}~~",
            embed_color=0x95A5A6,
            mention_everyone=False,
        )


def notify_blueprint_copy_request_providers(
    request,
    req: BlueprintCopyRequest,
    *,
    notification_title: str | None = None,
    notification_body: str | None = None,
    notify_user_fn=notify_user,
    send_webhook_fn=send_discord_webhook_with_message_id,
    build_action_link_fn=build_action_link,
) -> None:
    """Notify eligible providers for a blueprint copy request."""

    eligible_details = eligible_owner_details_for_request(req)
    eligible_owner_ids = set(eligible_details.owner_ids)
    if not eligible_owner_ids:
        return

    default_title, default_body, corporate_source_line = (
        build_blueprint_copy_request_notification_content(req)
    )

    resolved_title = notification_title or default_title
    resolved_body = notification_body or default_body

    fulfill_queue_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_fulfill_requests")
    )
    fulfill_label = _("Review copy requests")

    if notification_body is not None:
        corporate_source_line = ""

    muted_user_ids: set[int] = set()
    direct_user_ids: set[int] = set(eligible_details.character_owner_ids)

    for corp_id, corp_user_ids in eligible_details.corporate_members_by_corp.items():
        webhooks = NotificationWebhook.get_blueprint_sharing_webhooks(corp_id)
        if not webhooks:
            continue

        provider_body = resolved_body
        if corporate_source_line:
            provider_body = f"{provider_body}\n\n{corporate_source_line}"

        sent_any = False
        for webhook in webhooks:
            sent, message_id = send_webhook_fn(
                webhook.webhook_url,
                resolved_title,
                provider_body,
                level="info",
                link=fulfill_queue_url,
                thumbnail_url=None,
                embed_title=f"📘 {resolved_title}",
                embed_color=0x5865F2,
                mention_everyone=bool(getattr(webhook, "ping_here", False)),
            )
            if sent:
                sent_any = True
                if message_id:
                    NotificationWebhookMessage.objects.create(
                        webhook_type=NotificationWebhook.TYPE_BLUEPRINT_SHARING,
                        webhook_url=webhook.webhook_url,
                        message_id=message_id,
                        copy_request=req,
                    )

        if sent_any:
            muted_user_ids.update(set(corp_user_ids) - direct_user_ids)

    provider_users = User.objects.filter(
        id__in=(eligible_owner_ids - muted_user_ids),
        is_active=True,
    )

    base_url = request.build_absolute_uri("/")
    sent_to: set[int] = set()
    for owner in provider_users:
        if owner.id in sent_to:
            continue
        sent_to.add(owner.id)

        provider_body = resolved_body
        if corporate_source_line:
            provider_body = f"{provider_body}\n\n{corporate_source_line}"

        quick_actions = []
        link_cta = _("Click here")

        accept_link = build_action_link_fn(
            action="accept",
            request_id=req.id,
            user_id=owner.id,
            base_url=base_url,
        )
        if accept_link:
            quick_actions.append(
                _("Accept: %(link)s") % {"link": f"[{link_cta}]({accept_link})"}
            )

        conditional_link = build_action_link_fn(
            action="conditional",
            request_id=req.id,
            user_id=owner.id,
            base_url=base_url,
        )
        if conditional_link:
            quick_actions.append(
                _("Send conditions: %(link)s")
                % {"link": f"[{link_cta}]({conditional_link})"}
            )

        reject_link = build_action_link_fn(
            action="reject",
            request_id=req.id,
            user_id=owner.id,
            base_url=base_url,
        )
        if reject_link:
            quick_actions.append(
                _("Decline: %(link)s") % {"link": f"[{link_cta}]({reject_link})"}
            )

        if quick_actions:
            provider_body = (
                f"{provider_body}\n\n"
                f"{_('Quick actions:')}\n" + "\n".join(quick_actions)
            )

        notify_user_fn(
            owner,
            resolved_title,
            provider_body,
            "info",
            link=fulfill_queue_url,
            link_label=fulfill_label,
        )
