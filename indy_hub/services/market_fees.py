"""Database-backed market fee preferences for the Craft workspace."""

from __future__ import annotations

# Standard Library
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from ..models import CharacterSettings, IndustrySkillSnapshot

ACCOUNTING_SKILL_TYPE_ID = 16622
BROKER_RELATIONS_SKILL_TYPE_ID = 3446

SALES_TAX_BASE_PERCENT = Decimal("7.5")
ACCOUNTING_REDUCTION_PER_LEVEL = Decimal("0.11")
NPC_BROKER_FEE_BASE_PERCENT = Decimal("3.0")
BROKER_RELATIONS_REDUCTION_PERCENT = Decimal("0.3")
MARKET_FEE_PERCENT_QUANTUM = Decimal("0.01")
MARKET_FEE_PERCENT_MAX = Decimal("100.00")
MARKET_PURPOSE_MARKET_SALE = "market_sale"
MARKET_PURPOSE_PERSONAL_USE = "personal_use"
MARKET_PURPOSES = {MARKET_PURPOSE_MARKET_SALE, MARKET_PURPOSE_PERSONAL_USE}


def _clamp_skill_level(value: object) -> int:
    try:
        return max(0, min(int(value or 0), 5))
    except (TypeError, ValueError):
        return 0


def normalize_market_fee_percent(
    value: object,
    *,
    default: Decimal | None = None,
) -> Decimal | None:
    """Return a finite 0..100 percentage rounded to two decimal places."""

    if value is None or value == "":
        return default
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    if not normalized.is_finite():
        return default
    return min(max(normalized, Decimal("0")), MARKET_FEE_PERCENT_MAX).quantize(
        MARKET_FEE_PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def compute_sales_tax_percent(accounting_level: object) -> Decimal:
    """Compute the current EVE sales-tax rate from Accounting skill level."""

    level = Decimal(_clamp_skill_level(accounting_level))
    return SALES_TAX_BASE_PERCENT * (
        Decimal("1") - (ACCOUNTING_REDUCTION_PER_LEVEL * level)
    )


def compute_estimated_broker_fee_percent(broker_relations_level: object) -> Decimal:
    """Compute the NPC-station broker fee without standings adjustments."""

    level = Decimal(_clamp_skill_level(broker_relations_level))
    return max(
        Decimal("0"),
        NPC_BROKER_FEE_BASE_PERCENT - (BROKER_RELATIONS_REDUCTION_PERCENT * level),
    )


def build_craft_market_fee_profiles(user) -> dict[str, object]:
    """Serialize owned characters and cached fee inputs without any ESI access."""

    user_id = getattr(user, "pk", None)
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return {"characters": [], "default_character_id": None}

    ownerships = list(
        CharacterOwnership.objects.filter(user_id=user_id)
        .select_related("character")
        .order_by("character__character_name", "character__character_id")
    )
    character_ids = [
        int(ownership.character.character_id)
        for ownership in ownerships
        if ownership.character
    ]
    snapshots = {
        int(snapshot.character_id): snapshot
        for snapshot in IndustrySkillSnapshot.objects.filter(
            owner_user=user,
            character_id__in=character_ids,
        )
    }
    settings_by_character = {
        int(setting.character_id): setting
        for setting in CharacterSettings.objects.filter(
            user_id=user_id,
            character_id__in=character_ids,
        )
    }

    main_character_id = 0
    profile = getattr(user, "profile", None)
    main_character = getattr(profile, "main_character", None) if profile else None
    if main_character is not None:
        main_character_id = int(getattr(main_character, "character_id", 0) or 0)

    characters: list[dict[str, object]] = []
    for ownership in ownerships:
        character = ownership.character
        if character is None:
            continue
        character_id = int(character.character_id)
        snapshot = snapshots.get(character_id)
        setting = settings_by_character.get(character_id)
        accounting_level = (
            snapshot.get_skill_level(ACCOUNTING_SKILL_TYPE_ID) if snapshot else 0
        )
        broker_relations_level = (
            snapshot.get_skill_level(BROKER_RELATIONS_SKILL_TYPE_ID) if snapshot else 0
        )
        saved_broker_fee = (
            normalize_market_fee_percent(setting.market_broker_fee_percent)
            if setting and setting.market_broker_fee_percent is not None
            else None
        )
        characters.append(
            {
                "character_id": character_id,
                "name": str(character.character_name or character_id),
                "accounting_level": accounting_level,
                "broker_relations_level": broker_relations_level,
                "sales_tax_percent": float(compute_sales_tax_percent(accounting_level)),
                "estimated_broker_fee_percent": float(
                    compute_estimated_broker_fee_percent(broker_relations_level)
                ),
                "saved_broker_fee_percent": (
                    float(saved_broker_fee) if saved_broker_fee is not None else None
                ),
                "skills_missing": snapshot is None,
                "skills_last_updated": (
                    snapshot.last_updated.isoformat() if snapshot else None
                ),
            }
        )

    available_ids = {int(row["character_id"]) for row in characters}
    default_character_id = (
        main_character_id
        if main_character_id in available_ids
        else (int(characters[0]["character_id"]) if characters else None)
    )
    return {
        "characters": characters,
        "default_character_id": default_character_id,
    }


def sanitize_craft_market_fees(user, value: object) -> dict[str, object]:
    """Validate workspace fee state and restrict characters to the current user."""

    if not isinstance(value, dict):
        return {}
    raw_purpose = str(value.get("purpose") or "").strip().lower()
    purpose = raw_purpose if raw_purpose in MARKET_PURPOSES else ""
    try:
        character_id = int(value.get("sellerCharacterId") or 0)
    except (TypeError, ValueError):
        character_id = 0
    if (
        character_id > 0
        and not CharacterOwnership.objects.filter(
            user=user,
            character__character_id=character_id,
        ).exists()
    ):
        return {}
    if character_id <= 0 and not purpose:
        return {}

    broker_fee = normalize_market_fee_percent(
        value.get("brokerFeePercent"),
        default=Decimal("0"),
    )
    safety_tax = normalize_market_fee_percent(
        value.get("safetyTaxPercent"),
        default=Decimal("0"),
    )
    return {
        "purpose": purpose,
        "sellerCharacterId": character_id or None,
        "brokerFeePercent": float(broker_fee or 0),
        "safetyTaxPercent": float(safety_tax or 0),
    }


def persist_craft_market_fee_preference(user, market_fees: object) -> None:
    """Persist the broker fee for one validated owned character."""

    normalized = sanitize_craft_market_fees(user, market_fees)
    if (
        not normalized
        or normalized.get("purpose") == MARKET_PURPOSE_PERSONAL_USE
        or not normalized.get("sellerCharacterId")
    ):
        return
    CharacterSettings.objects.update_or_create(
        user=user,
        character_id=int(normalized["sellerCharacterId"]),
        defaults={
            "market_broker_fee_percent": normalize_market_fee_percent(
                normalized["brokerFeePercent"],
                default=Decimal("0"),
            )
        },
    )
