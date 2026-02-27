"""Helpers for Indy Hub menu badge count computation."""

# Django
from django.db.models import Exists, F, OuterRef, Q


def compute_menu_badge_count(user_id: int) -> int:
    """Compute pending Indy Hub menu badge count for a user."""
    from ..models import Blueprint, BlueprintCopyChat, BlueprintCopyRequest

    pending_request_ids: set[int] = set()

    provider_blueprints = Blueprint.objects.filter(
        owner_user_id=user_id,
        bp_type__in=[
            Blueprint.BPType.ORIGINAL,
            Blueprint.BPType.REACTION,
        ],
        type_id=OuterRef("type_id"),
        material_efficiency=OuterRef("material_efficiency"),
        time_efficiency=OuterRef("time_efficiency"),
    )

    fulfill_qs = (
        BlueprintCopyRequest.objects.annotate(can_fulfill=Exists(provider_blueprints))
        .filter(can_fulfill=True)
        .filter(
            Q(fulfilled=False)
            | Q(
                fulfilled=True,
                delivered=False,
                offers__owner_id=user_id,
            )
        )
        .exclude(requested_by_id=user_id)
        .exclude(
            offers__owner_id=user_id,
            offers__status="rejected",
        )
        .distinct()
    )
    pending_request_ids.update(fulfill_qs.values_list("id", flat=True))

    unread_chat_qs = BlueprintCopyChat.objects.filter(
        is_open=True,
        last_message_at__isnull=False,
    ).filter(
        (
            Q(buyer_id=user_id, last_message_role="seller")
            & (
                Q(buyer_last_seen_at__isnull=True)
                | Q(buyer_last_seen_at__lt=F("last_message_at"))
            )
        )
        | (
            Q(seller_id=user_id, last_message_role="buyer")
            & (
                Q(seller_last_seen_at__isnull=True)
                | Q(seller_last_seen_at__lt=F("last_message_at"))
            )
        )
    )

    pending_request_ids.update(unread_chat_qs.values_list("request_id", flat=True))
    return len(pending_request_ids)
