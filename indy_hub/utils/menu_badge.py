"""Helpers for Indy Hub menu badge count computation."""

# Django
from django.core.cache import cache
from django.db.models import Exists, F, OuterRef, Q

MENU_BADGE_CACHE_TTL_SECONDS = 45

# Required personal-character ESI scopes for Indy Hub. Kept in sync with
# ``indy_hub.views.user`` (BLUEPRINT/JOBS/ASSETS/SKILLS/ONLINE scope sets).
_BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
_JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
_STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
_SKILLS_SCOPE = "esi-skills.read_skills.v1"
_ASSETS_SCOPE = "esi-assets.read_assets.v1"
_ONLINE_SCOPE = "esi-location.read_online.v1"
_CHARACTER_REQUIRED_SCOPES = (
    _BLUEPRINT_SCOPE,
    _JOBS_SCOPE,
    _STRUCTURE_SCOPE,
    _SKILLS_SCOPE,
    _ASSETS_SCOPE,
    _ONLINE_SCOPE,
)


def menu_badge_cache_key(user_id: int) -> str:
    return f"indy_hub:menu_badge_count:{int(user_id)}"


def menu_badge_refresh_lock_key(user_id: int) -> str:
    return f"indy_hub:menu_badge_count_refreshing:{int(user_id)}"


def invalidate_menu_badge_cache(*user_ids: int | None) -> None:
    for user_id in {int(user_id) for user_id in user_ids if user_id}:
        cache.delete(menu_badge_cache_key(user_id))
        cache.delete(menu_badge_refresh_lock_key(user_id))


def count_material_exchange_open_orders(user_id: int) -> int:
    """Return the number of open Material Exchange orders for a user."""
    from ..models import MaterialExchangeBuyOrder, MaterialExchangeSellOrder

    closed_statuses = [
        MaterialExchangeSellOrder.Status.COMPLETED,
        MaterialExchangeSellOrder.Status.REJECTED,
        MaterialExchangeSellOrder.Status.CANCELLED,
    ]
    sell_count = MaterialExchangeSellOrder.objects.filter(seller_id=user_id).exclude(
        status__in=closed_statuses
    ).count()
    buy_count = MaterialExchangeBuyOrder.objects.filter(buyer_id=user_id).exclude(
        status__in=closed_statuses
    ).count()
    return int(sell_count) + int(buy_count)


def compute_menu_badge_count(user_id: int) -> int:
    """Compute pending Indy Hub menu badge count for a user."""
    from ..models import Blueprint, BlueprintCopyChat, BlueprintCopyRequest

    pending_request_ids: set[int] = set()

    my_requests_qs = BlueprintCopyRequest.objects.filter(
        requested_by_id=user_id
    ).filter(Q(fulfilled=False) | Q(fulfilled=True, delivered=False))
    pending_request_ids.update(my_requests_qs.values_list("id", flat=True))

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
    return (
        len(pending_request_ids)
        + count_material_exchange_open_orders(user_id)
        + count_characters_missing_scopes(user_id)
    )


def count_characters_missing_scopes(user_id: int) -> int:
    """Return the number of the user's characters missing at least one required Indy Hub scope.

    Only characters that already have at least one valid token are counted, so
    brand-new users that never authorized Indy Hub do not see a navbar warning.
    The goal is to surface freshly added scope requirements (e.g. a new
    ``esi-location.read_online.v1`` requirement after an upgrade) on accounts
    that are already partially linked.
    Returns ``0`` silently when Alliance Auth's character or token models are
    not available (e.g. minimal test environments).
    """
    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership
        from esi.models import Token
    except Exception:
        return 0

    ownerships = CharacterOwnership.objects.filter(user_id=user_id).values_list(
        "character__character_id", flat=True
    )
    if not ownerships:
        return 0

    missing_count = 0
    for character_id in ownerships:
        char_tokens = Token.objects.filter(
            user_id=user_id, character_id=character_id
        ).require_valid()
        if not char_tokens.exists():
            # Character was never linked to Indy Hub — don't pollute the badge.
            continue
        for scope in _CHARACTER_REQUIRED_SCOPES:
            if not char_tokens.require_scopes([scope]).exists():
                missing_count += 1
                break
    return missing_count
