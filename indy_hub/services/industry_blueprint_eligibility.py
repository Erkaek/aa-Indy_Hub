"""Identity and eligibility helpers for blueprint copy request providers."""

from __future__ import annotations

# Standard Library
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Django
from django.contrib.auth.models import User
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile

from ..models import (
    Blueprint,
    BlueprintCopyRequest,
    CharacterSettings,
    CorporationSharingSetting,
)
from ..utils.eve import (
    get_character_name,
    get_corporation_name,
    get_corporation_ticker,
)


@dataclass
class EligibleOwnerDetails:
    owner_ids: set[int]
    character_owner_ids: set[int]
    corporate_members_by_corp: dict[int, set[int]]
    user_to_corporation: dict[int, int]


@dataclass
class UserIdentity:
    user_id: int
    username: str
    character_id: int | None
    character_name: str
    corporation_id: int | None
    corporation_name: str
    corporation_ticker: str


def resolve_user_identity(user: User | None) -> UserIdentity:
    """Best-effort resolution of a user's primary character and corporation."""

    if not user:
        return UserIdentity(
            user_id=0,
            username="",
            character_id=None,
            character_name="",
            corporation_id=None,
            corporation_name="",
            corporation_ticker="",
        )

    username = user.username
    character_name = username
    corporation_name = ""
    corporation_ticker = ""
    character_id: int | None = None
    corporation_id: int | None = None

    main_character = None
    profile = getattr(user, "profile", None)
    if profile and getattr(profile, "main_character_id", None):
        main_character = getattr(profile, "main_character", None)

    if not main_character:
        try:
            profile = UserProfile.objects.select_related("main_character").get(
                user=user
            )
        except UserProfile.DoesNotExist:
            profile = None
        else:
            main_character = getattr(profile, "main_character", None)

    if not main_character:
        ownership_qs = CharacterOwnership.objects.filter(user=user).select_related(
            "character"
        )
        try:
            CharacterOwnership._meta.get_field("is_main")
        except FieldDoesNotExist:
            ownership = ownership_qs.first()
        else:
            ownership = ownership_qs.order_by("-is_main").first()
        if ownership:
            main_character = getattr(ownership, "character", None)

    if main_character:
        character_id = getattr(main_character, "character_id", None)
        corporation_id = getattr(main_character, "corporation_id", None)
        character_name = (
            get_character_name(character_id)
            or getattr(main_character, "character_name", None)
            or username
        )
        corporation_name = (
            get_corporation_name(corporation_id)
            or getattr(main_character, "corporation_name", None)
            or ""
        )
        if corporation_id:
            corp_attr_ticker = getattr(main_character, "corporation_ticker", "")
            corporation_ticker = corp_attr_ticker or get_corporation_ticker(
                corporation_id
            )
        else:
            corporation_ticker = ""

    return UserIdentity(
        user_id=user.id,
        username=username,
        character_id=character_id,
        character_name=character_name,
        corporation_id=corporation_id,
        corporation_name=corporation_name,
        corporation_ticker=corporation_ticker,
    )


def get_explicit_corp_bp_manager_ids() -> set[int]:
    """Return active users with explicit corp BP manager permission."""

    return set(
        User.objects.filter(
            Q(user_permissions__codename="can_manage_corp_bp_requests")
            | Q(groups__permissions__codename="can_manage_corp_bp_requests"),
            is_active=True,
        ).values_list("id", flat=True)
    )


def eligible_owner_details_for_request(
    req: BlueprintCopyRequest,
) -> EligibleOwnerDetails:
    """Return detailed information about users who can fulfil a request."""

    matching_blueprints = Blueprint.objects.filter(
        bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION],
        type_id=req.type_id,
        material_efficiency=req.material_efficiency,
        time_efficiency=req.time_efficiency,
    )

    character_owned_blueprints = list(
        matching_blueprints.filter(owner_kind=Blueprint.OwnerKind.CHARACTER).values(
            "owner_user_id", "character_id"
        )
    )

    character_owner_ids: set[int] = set()
    if character_owned_blueprints:
        owner_user_ids = {bp["owner_user_id"] for bp in character_owned_blueprints}
        allowed_settings = CharacterSettings.objects.filter(
            user_id__in=owner_user_ids,
            allow_copy_requests=True,
        ).values("user_id", "character_id")

        allowed_map: dict[int, set[int]] = defaultdict(set)
        for setting in allowed_settings:
            allowed_map[setting["user_id"]].add(setting["character_id"])

        for bp in character_owned_blueprints:
            user_id = bp["owner_user_id"]
            if not user_id:
                continue
            char_id = bp["character_id"]
            allowed_chars = allowed_map.get(user_id)
            if not allowed_chars:
                continue
            if 0 in allowed_chars:
                character_owner_ids.add(user_id)
                continue
            if char_id is None:
                if allowed_chars:
                    character_owner_ids.add(user_id)
                continue
            if char_id in allowed_chars:
                character_owner_ids.add(user_id)

    corporation_ids = list(
        matching_blueprints.filter(owner_kind=Blueprint.OwnerKind.CORPORATION)
        .exclude(corporation_id__isnull=True)
        .values_list("corporation_id", flat=True)
        .distinct()
    )

    corporate_settings: list[CorporationSharingSetting] = []
    corporate_owner_ids: set[int] = set()
    corporate_members_by_corp: dict[int, set[int]] = defaultdict(set)
    user_to_corp: dict[int, int] = {}

    explicit_corp_manager_ids = get_explicit_corp_bp_manager_ids()

    if corporation_ids:
        corporate_settings = list(
            CorporationSharingSetting.objects.filter(
                corporation_id__in=corporation_ids,
                allow_copy_requests=True,
                share_scope__in=[
                    CharacterSettings.SCOPE_CORPORATION,
                    CharacterSettings.SCOPE_ALLIANCE,
                    CharacterSettings.SCOPE_EVERYONE,
                ],
            )
        )
        for setting in corporate_settings:
            corp_id = setting.corporation_id
            if corp_id is None:
                continue
            corporate_members_by_corp[corp_id].add(setting.user_id)
            user_to_corp[setting.user_id] = corp_id
        corporate_owner_ids = {setting.user_id for setting in corporate_settings}

    additional_corp_manager_ids: set[int] = set()
    if corporation_ids and corporate_settings and explicit_corp_manager_ids:
        settings_by_corp: dict[int, list[CorporationSharingSetting]] = defaultdict(list)
        for setting_obj in corporate_settings:
            settings_by_corp[setting_obj.corporation_id].append(setting_obj)

        corp_memberships = CharacterOwnership.objects.filter(
            character__corporation_id__in=corporation_ids
        ).values("user_id", "character__corporation_id", "character__character_id")

        corp_user_chars: dict[int, dict[int, set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        corp_member_user_ids: set[int] = set()
        for membership in corp_memberships:
            corp_id = membership.get("character__corporation_id")
            user_id = membership.get("user_id")
            char_id = membership.get("character__character_id")
            if corp_id and user_id:
                corp_user_chars[corp_id][user_id].add(char_id)
                corp_member_user_ids.add(user_id)

        if corp_member_user_ids:
            corp_manager_ids = explicit_corp_manager_ids.intersection(
                corp_member_user_ids
            )

            for corp_id, users in corp_user_chars.items():
                corp_settings = settings_by_corp.get(corp_id)
                if not corp_settings:
                    continue
                for user_id, char_ids in users.items():
                    if user_id not in corp_manager_ids:
                        continue
                    if user_id in corporate_owner_ids:
                        continue
                    if user_id == req.requested_by_id:
                        continue
                    if any(
                        not setting_obj.restricts_characters
                        or any(
                            setting_obj.is_character_authorized(char_id)
                            for char_id in char_ids
                        )
                        for setting_obj in corp_settings
                    ):
                        additional_corp_manager_ids.add(user_id)
                        corporate_members_by_corp[corp_id].add(user_id)
                        user_to_corp[user_id] = corp_id

    owner_ids: set[int] = (
        set(character_owner_ids) | corporate_owner_ids | additional_corp_manager_ids
    )

    owner_ids.discard(req.requested_by_id)
    character_owner_ids.discard(req.requested_by_id)
    for members in corporate_members_by_corp.values():
        members.discard(req.requested_by_id)

    user_to_corp = {uid: cid for uid, cid in user_to_corp.items() if uid in owner_ids}
    corporate_members_by_corp = {
        corp_id: {uid for uid in members if uid in owner_ids}
        for corp_id, members in corporate_members_by_corp.items()
        if members
    }

    return EligibleOwnerDetails(
        owner_ids=owner_ids,
        character_owner_ids=set(character_owner_ids),
        corporate_members_by_corp=corporate_members_by_corp,
        user_to_corporation=user_to_corp,
    )


def build_eligible_owner_ids_map(
    requester: User,
    blueprint_keys: Iterable[tuple[int, int, int]],
) -> dict[tuple[int, int, int], set[int]]:
    """Batched equivalent of eligible_owner_details_for_request for many keys."""

    keys: set[tuple[int, int, int]] = {
        (int(type_id), int(me), int(te)) for type_id, me, te in blueprint_keys
    }
    if not keys:
        return {}

    type_ids = {key[0] for key in keys}

    blueprint_rows = Blueprint.objects.filter(
        bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION],
        type_id__in=type_ids,
    ).values(
        "type_id",
        "material_efficiency",
        "time_efficiency",
        "owner_kind",
        "owner_user_id",
        "character_id",
        "corporation_id",
    )

    char_bps_by_key: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(
        list
    )
    corp_ids_by_key: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    all_char_owner_user_ids: set[int] = set()
    all_corp_ids: set[int] = set()

    for row in blueprint_rows:
        key = (
            int(row["type_id"]),
            int(row["material_efficiency"]),
            int(row["time_efficiency"]),
        )
        if key not in keys:
            continue
        if row["owner_kind"] == Blueprint.OwnerKind.CHARACTER:
            char_bps_by_key[key].append(row)
            if row["owner_user_id"]:
                all_char_owner_user_ids.add(row["owner_user_id"])
        elif (
            row["owner_kind"] == Blueprint.OwnerKind.CORPORATION
            and row["corporation_id"]
        ):
            corp_ids_by_key[key].add(int(row["corporation_id"]))
            all_corp_ids.add(int(row["corporation_id"]))

    allowed_char_map: dict[int, set[int]] = defaultdict(set)
    if all_char_owner_user_ids:
        for setting in CharacterSettings.objects.filter(
            user_id__in=all_char_owner_user_ids,
            allow_copy_requests=True,
        ).values("user_id", "character_id"):
            allowed_char_map[setting["user_id"]].add(setting["character_id"])

    corp_settings_by_corp: dict[int, list[CorporationSharingSetting]] = defaultdict(
        list
    )
    if all_corp_ids:
        for setting in CorporationSharingSetting.objects.filter(
            corporation_id__in=all_corp_ids,
            allow_copy_requests=True,
            share_scope__in=[
                CharacterSettings.SCOPE_CORPORATION,
                CharacterSettings.SCOPE_ALLIANCE,
                CharacterSettings.SCOPE_EVERYONE,
            ],
        ):
            if setting.corporation_id:
                corp_settings_by_corp[int(setting.corporation_id)].append(setting)

    explicit_corp_manager_ids: set[int] = set()
    corp_user_chars: dict[int, dict[int, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    if corp_settings_by_corp:
        explicit_corp_manager_ids = get_explicit_corp_bp_manager_ids()
        if explicit_corp_manager_ids:
            for membership in CharacterOwnership.objects.filter(
                character__corporation_id__in=set(corp_settings_by_corp.keys()),
            ).values("user_id", "character__corporation_id", "character__character_id"):
                corp_id = membership.get("character__corporation_id")
                user_id = membership.get("user_id")
                char_id = membership.get("character__character_id")
                if corp_id and user_id:
                    corp_user_chars[int(corp_id)][int(user_id)].add(char_id)

    requester_id = requester.id
    result: dict[tuple[int, int, int], set[int]] = {}
    for key in keys:
        owner_ids: set[int] = set()

        for bp in char_bps_by_key.get(key, []):
            user_id = bp.get("owner_user_id")
            if not user_id:
                continue
            char_id = bp.get("character_id")
            allowed_chars = allowed_char_map.get(user_id)
            if not allowed_chars:
                continue
            if 0 in allowed_chars or char_id is None or char_id in allowed_chars:
                owner_ids.add(int(user_id))

        key_corp_ids = corp_ids_by_key.get(key, set())
        key_corp_settings: list[CorporationSharingSetting] = []
        corporate_owner_ids: set[int] = set()
        for corp_id in key_corp_ids:
            for setting in corp_settings_by_corp.get(corp_id, []):
                owner_ids.add(int(setting.user_id))
                corporate_owner_ids.add(int(setting.user_id))
                key_corp_settings.append(setting)

        if key_corp_settings and explicit_corp_manager_ids:
            settings_by_corp_local: dict[int, list[CorporationSharingSetting]] = (
                defaultdict(list)
            )
            for setting in key_corp_settings:
                settings_by_corp_local[int(setting.corporation_id)].append(setting)
            for corp_id, users in corp_user_chars.items():
                if corp_id not in key_corp_ids:
                    continue
                local_settings = settings_by_corp_local.get(corp_id)
                if not local_settings:
                    continue
                for user_id, char_ids in users.items():
                    if user_id not in explicit_corp_manager_ids:
                        continue
                    if user_id in corporate_owner_ids:
                        continue
                    if user_id == requester_id:
                        continue
                    if any(
                        not setting_obj.restricts_characters
                        or any(
                            setting_obj.is_character_authorized(char_id)
                            for char_id in char_ids
                        )
                        for setting_obj in local_settings
                    ):
                        owner_ids.add(int(user_id))

        owner_ids.discard(requester_id)
        result[key] = owner_ids

    return result
