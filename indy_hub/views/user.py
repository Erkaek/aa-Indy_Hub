# User-related views
# Standard Library
import json
import logging
import secrets
from collections.abc import Iterable
from math import ceil
from typing import Any
from urllib.parse import urlencode

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from esi.models import CallbackRedirect, Token

# AA Example App
from indy_hub.models import CharacterSettings, CorporationSharingSetting

from ..decorators import indy_hub_access_required
from ..models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyRequest,
    IndustryJob,
    ProductionConfig,
    ProductionSimulation,
    UserOnboardingProgress,
)
from ..services.esi_client import ESIClientError, ESITokenError
from ..services.simulations import summarize_simulations
from ..tasks.industry import (
    CORP_BLUEPRINT_SCOPE,
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_JOBS_SCOPE,
    CORP_JOBS_SCOPE_SET,
    CORP_ROLES_SCOPE,
    MANUAL_REFRESH_KIND_BLUEPRINTS,
    MANUAL_REFRESH_KIND_JOBS,
    REQUIRED_CORPORATION_ROLES,
    get_character_corporation_roles,
    request_manual_refresh,
)
from ..utils.eve import get_character_name, get_corporation_name, get_type_name
from .navigation import build_nav_context

logger = logging.getLogger(__name__)


ONBOARDING_TASK_CONFIG = [
    {
        "key": "connect_blueprints",
        "title": _("Connect blueprint access"),
        "description": _(
            "Authorize at least one character so Indy Hub can import your blueprints."
        ),
        "mode": "auto",
        "cta": "indy_hub:token_management",
        "icon": "fa-scroll",
    },
    {
        "key": "connect_jobs",
        "title": _("Connect industry jobs"),
        "description": _(
            "Add an industry jobs token to track active slots and completions."
        ),
        "mode": "auto",
        "cta": "indy_hub:token_management",
        "icon": "fa-industry",
    },
    {
        "key": "enable_sharing",
        "title": _("Enable copy sharing"),
        "description": _(
            "Pick a sharing scope so corpmates can request copies from your originals."
        ),
        "mode": "auto",
        "cta": "indy_hub:index",
        "icon": "fa-share-alt",
    },
    {
        "key": "review_guides",
        "title": _("Review the quick-start guides"),
        "description": _(
            "Skim the journey cards on the request or fulfil pages to learn the flow."
        ),
        "mode": "manual",
        "cta": "indy_hub:bp_copy_request_page",
        "icon": "fa-compass",
    },
    {
        "key": "submit_request",
        "title": _("Submit your first copy request"),
        "description": _("Try the workflow end to end by requesting a blueprint copy."),
        "mode": "auto",
        "cta": "indy_hub:bp_copy_request_page",
        "icon": "fa-copy",
    },
]

MANUAL_ONBOARDING_KEYS = {
    cfg["key"] for cfg in ONBOARDING_TASK_CONFIG if cfg["mode"] == "manual"
}

BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
BLUEPRINT_SCOPE_SET = [BLUEPRINT_SCOPE, STRUCTURE_SCOPE]
JOBS_SCOPE_SET = [JOBS_SCOPE, STRUCTURE_SCOPE]


def _build_corporation_authorization_summary(
    setting: CorporationSharingSetting | None,
) -> dict[str, Any]:
    if not setting:
        return {
            "restricted": False,
            "characters": [],
            "authorized_count": 0,
            "has_authorized": False,
        }

    characters: list[dict[str, Any]] = []
    for char_id in setting.authorized_character_ids:
        characters.append(
            {
                "id": char_id,
                "name": get_character_name(char_id),
            }
        )

    return {
        "restricted": True,
        "characters": characters,
        "authorized_count": len(characters),
        "has_authorized": bool(characters),
    }


def _collect_corporation_scope_status(
    user, *, include_warnings: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not user.has_perm("indy_hub.can_manage_corporate_assets"):
        empty: list[dict[str, Any]] = []
        return (empty, []) if include_warnings else empty

    if not Token:
        empty: list[dict[str, Any]] = []
        return (empty, []) if include_warnings else empty

    ownerships = CharacterOwnership.objects.filter(user=user).select_related(
        "character"
    )
    settings_map = {
        setting.corporation_id: setting
        for setting in CorporationSharingSetting.objects.filter(user=user)
    }
    corp_status: dict[int, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = [] if include_warnings else []

    def _revoke_corporation_tokens(
        token_queryset,
        character_id: int,
        character_name: str | None,
        corporation_id: int,
        corporation_name: str | None,
        *,
        scopes_to_revoke: Iterable[str] | None = None,
    ) -> int:
        if not Token:
            return 0

        normalized_scopes = sorted({scope for scope in scopes_to_revoke or [] if scope})
        if not normalized_scopes:
            return 0

        token_ids: set[int] = set()
        for scope in normalized_scopes:
            token_ids.update(
                token_queryset.require_scopes([scope]).values_list("pk", flat=True)
            )

        if not token_ids:
            return 0

        token_queryset.filter(pk__in=token_ids).delete()
        logger.info(
            "Revoked %s corporate tokens for character %s (%s) and corporation %s (%s)",
            len(token_ids),
            character_id,
            character_name,
            corporation_id,
            corporation_name,
            extra={"scopes": normalized_scopes},
        )
        return len(token_ids)

    def _select_corporation_token(token_qs, primary_scope: str):
        """Return the newest token covering the scope (roles required, structures optional)."""

        scope_sets = (
            [primary_scope, CORP_ROLES_SCOPE, STRUCTURE_SCOPE],
            [primary_scope, CORP_ROLES_SCOPE],
        )
        seen: set[tuple[str, ...]] = set()
        for scope_list in scope_sets:
            normalized = tuple(sorted(scope_list))
            if normalized in seen:
                continue
            seen.add(normalized)
            candidate = token_qs.require_scopes(scope_list)
            token = candidate.order_by("-created").first()
            if token:
                return token
        return None

    for ownership in ownerships:
        corp_id = getattr(ownership.character, "corporation_id", None)
        if not corp_id:
            continue

        corp_name = get_corporation_name(corp_id) or str(corp_id)
        setting = settings_map.get(corp_id)
        if setting is None:
            setting, _ = CorporationSharingSetting.objects.get_or_create(
                user=user,
                corporation_id=corp_id,
                defaults={
                    "corporation_name": corp_name,
                    "share_scope": CharacterSettings.SCOPE_NONE,
                    "allow_copy_requests": False,
                },
            )
            settings_map[corp_id] = setting
        elif corp_name and setting.corporation_name != corp_name:
            setting.corporation_name = corp_name
            setting.save(update_fields=["corporation_name", "updated_at"])

        character_id = ownership.character.character_id
        character_name = get_character_name(character_id)
        token_qs = Token.objects.filter(user=user, character_id=character_id)
        if not token_qs.exists():
            continue

        if (
            setting
            and setting.restricts_characters
            and not setting.is_character_authorized(character_id)
        ):
            logger.debug(
                "Character %s ignored for corporation %s: not authorised for Indy Hub",
                character_id,
                corp_id,
            )
            continue

        blueprint_token = _select_corporation_token(token_qs, CORP_BLUEPRINT_SCOPE)
        jobs_token = _select_corporation_token(token_qs, CORP_JOBS_SCOPE)
        if not blueprint_token and not jobs_token:
            continue

        try:
            roles = get_character_corporation_roles(character_id)
        except ESITokenError:
            logger.info(
                "Character %s lacks corporation roles scope for corporation %s",
                character_id,
                corp_id,
            )
            scopes_to_revoke: list[str] = []
            if blueprint_token:
                scopes_to_revoke.append(CORP_BLUEPRINT_SCOPE)
            if jobs_token:
                scopes_to_revoke.append(CORP_JOBS_SCOPE)
            revoked_count = _revoke_corporation_tokens(
                token_qs,
                character_id,
                character_name,
                corp_id,
                corp_name,
                scopes_to_revoke=scopes_to_revoke,
            )
            if include_warnings:
                warnings.append(
                    {
                        "reason": "missing_roles_scope",
                        "character_id": character_id,
                        "character_name": character_name,
                        "corporation_id": corp_id,
                        "corporation_name": corp_name,
                        "tokens_revoked": bool(revoked_count),
                        "revoked_token_count": revoked_count,
                        "revoked_token_scopes": sorted(set(scopes_to_revoke)),
                    }
                )
            continue
        except ESIClientError as exc:
            logger.warning(
                "Unable to load corporation roles for character %s (corporation %s): %s",
                character_id,
                corp_id,
                exc,
            )
            continue
        if not roles.intersection(REQUIRED_CORPORATION_ROLES):
            logger.info(
                "Character %s lacks required roles %s for corporation %s",
                character_id,
                ", ".join(sorted(REQUIRED_CORPORATION_ROLES)),
                corp_id,
            )
            scopes_to_revoke = []
            if blueprint_token:
                scopes_to_revoke.append(CORP_BLUEPRINT_SCOPE)
            if jobs_token:
                scopes_to_revoke.append(CORP_JOBS_SCOPE)
            revoked_count = _revoke_corporation_tokens(
                token_qs,
                character_id,
                character_name,
                corp_id,
                corp_name,
                scopes_to_revoke=scopes_to_revoke,
            )
            if include_warnings:
                warnings.append(
                    {
                        "reason": "missing_required_roles",
                        "character_id": character_id,
                        "character_name": character_name,
                        "corporation_id": corp_id,
                        "corporation_name": corp_name,
                        "character_roles": sorted(roles),
                        "required_roles": sorted(REQUIRED_CORPORATION_ROLES),
                        "tokens_revoked": bool(revoked_count),
                        "revoked_token_count": revoked_count,
                        "revoked_token_scopes": sorted(set(scopes_to_revoke)),
                    }
                )
            continue

        entry = corp_status.setdefault(
            corp_id,
            {
                "corporation_id": corp_id,
                "corporation_name": corp_name,
                "blueprint": {
                    "has_scope": False,
                    "character_id": None,
                    "character_name": None,
                    "last_updated": None,
                },
                "jobs": {
                    "has_scope": False,
                    "character_id": None,
                    "character_name": None,
                    "last_updated": None,
                },
                "authorization": _build_corporation_authorization_summary(setting),
            },
        )

        if entry.get("corporation_name") != corp_name:
            entry["corporation_name"] = corp_name

        entry["authorization"] = _build_corporation_authorization_summary(setting)

        if blueprint_token and not entry["blueprint"]["has_scope"]:
            entry["blueprint"] = {
                "has_scope": True,
                "character_id": character_id,
                "character_name": character_name,
                "last_updated": getattr(blueprint_token, "created", None),
            }

        if jobs_token and not entry["jobs"]["has_scope"]:
            entry["jobs"] = {
                "has_scope": True,
                "character_id": character_id,
                "character_name": character_name,
                "last_updated": getattr(jobs_token, "created", None),
            }

    result = sorted(
        corp_status.values(), key=lambda item: (item["corporation_name"] or "")
    )
    if include_warnings:
        return result, warnings
    return result


def _default_corporation_summary_entry(
    corporation_id: int, corporation_name: str | None
) -> dict[str, Any]:
    display_name = (
        corporation_name or get_corporation_name(corporation_id) or str(corporation_id)
    )
    empty_token = {
        "has_scope": False,
        "character_id": None,
        "character_name": None,
        "last_updated": None,
    }

    return {
        "corporation_id": corporation_id,
        "name": display_name,
        "blueprints": {
            "total": 0,
            "originals": 0,
            "copies": 0,
            "reactions": 0,
            "last_sync": None,
            "token": empty_token.copy(),
        },
        "jobs": {
            "total": 0,
            "active": 0,
            "completed": 0,
            "last_sync": None,
            "token": empty_token.copy(),
        },
        "authorization": {
            "restricted": False,
            "characters": [],
        },
    }


def build_corporation_sharing_context(user) -> dict[str, Any] | None:
    if not user.has_perm("indy_hub.can_manage_corporate_assets"):
        return None

    corp_scope_status = _collect_corporation_scope_status(user)
    summary: dict[int, dict[str, Any]] = {}

    for entry in corp_scope_status:
        corp_id = entry.get("corporation_id")
        if not corp_id:
            continue
        corp_name = entry.get("corporation_name")
        summary_entry = _default_corporation_summary_entry(corp_id, corp_name)
        summary_entry["blueprints"]["token"] = dict(entry.get("blueprint", {}) or {})
        summary_entry["jobs"]["token"] = dict(entry.get("jobs", {}) or {})
        summary_entry["authorization"] = dict(entry.get("authorization", {}) or {})
        summary[corp_id] = summary_entry

    blueprint_rows = (
        Blueprint.objects.filter(
            owner_user=user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
        )
        .values("corporation_id", "corporation_name")
        .annotate(
            total=Count("id"),
            originals=Count("id", filter=Q(bp_type=Blueprint.BPType.ORIGINAL)),
            copies=Count("id", filter=Q(bp_type=Blueprint.BPType.COPY)),
            reactions=Count("id", filter=Q(bp_type=Blueprint.BPType.REACTION)),
            last_sync=Max("last_updated"),
        )
    )

    for row in blueprint_rows:
        corp_id = row.get("corporation_id")
        if not corp_id:
            continue
        corp_name = row.get("corporation_name")
        entry = summary.setdefault(
            corp_id, _default_corporation_summary_entry(corp_id, corp_name)
        )
        entry["name"] = (
            entry.get("name")
            or corp_name
            or get_corporation_name(corp_id)
            or str(corp_id)
        )
        entry["blueprints"].update(
            {
                "total": row.get("total", 0) or 0,
                "originals": row.get("originals", 0) or 0,
                "copies": row.get("copies", 0) or 0,
                "reactions": row.get("reactions", 0) or 0,
                "last_sync": row.get("last_sync"),
            }
        )

    now = timezone.now()
    jobs_rows = (
        IndustryJob.objects.filter(
            owner_user=user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
        )
        .values("corporation_id", "corporation_name")
        .annotate(
            total=Count("id"),
            active=Count("id", filter=Q(status__iexact="active") & Q(end_date__gt=now)),
            completed=Count(
                "id",
                filter=Q(status__in=["delivered", "ready"]) | Q(end_date__lte=now),
            ),
            last_sync=Max("last_updated"),
        )
    )

    for row in jobs_rows:
        corp_id = row.get("corporation_id")
        if not corp_id:
            continue
        corp_name = row.get("corporation_name")
        entry = summary.setdefault(
            corp_id, _default_corporation_summary_entry(corp_id, corp_name)
        )
        entry["name"] = (
            entry.get("name")
            or corp_name
            or get_corporation_name(corp_id)
            or str(corp_id)
        )
        entry["jobs"].update(
            {
                "total": row.get("total", 0) or 0,
                "active": row.get("active", 0) or 0,
                "completed": row.get("completed", 0) or 0,
                "last_sync": row.get("last_sync"),
            }
        )

    corporations = sorted(
        summary.values(),
        key=lambda item: (item.get("name") or str(item.get("corporation_id"))).lower(),
    )

    total_blueprints = sum(corp["blueprints"]["total"] for corp in corporations)
    total_jobs = sum(corp["jobs"]["total"] for corp in corporations)
    has_authorised = any(
        (
            corp["blueprints"]["token"].get("has_scope")
            or corp["jobs"]["token"].get("has_scope")
        )
        for corp in corporations
    )
    restricted_manual_tokens = sum(
        1 for corp in corporations if corp.get("authorization", {}).get("restricted")
    )

    return {
        "corporations": corporations,
        "has_corporations": bool(corporations),
        "total_blueprints": total_blueprints,
        "total_jobs": total_jobs,
        "has_authorised_characters": has_authorised,
        "restricted_corporation_tokens": restricted_manual_tokens,
        "token_management_url": reverse("indy_hub:token_management"),
        "required_roles": sorted(REQUIRED_CORPORATION_ROLES),
        "scopes": {
            "blueprints": CORP_BLUEPRINT_SCOPE,
            "jobs": CORP_JOBS_SCOPE,
            "roles": CORP_ROLES_SCOPE,
            "structures": STRUCTURE_SCOPE,
        },
    }


def _build_corporation_share_controls(
    user, corp_scope_status: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Prepare corporation sharing controls for the dashboard."""

    copy_states = get_copy_sharing_states()
    default_state = copy_states[CharacterSettings.SCOPE_NONE]
    settings_map = {
        setting.corporation_id: setting
        for setting in CorporationSharingSetting.objects.filter(user=user)
    }
    controls: list[dict[str, Any]] = []

    for entry in corp_scope_status:
        corp_id = entry.get("corporation_id")
        if not corp_id:
            continue
        corp_name = entry.get("corporation_name") or str(corp_id)
        setting = settings_map.get(corp_id)
        share_scope = setting.share_scope if setting else CharacterSettings.SCOPE_NONE
        state = copy_states.get(share_scope, default_state)

        controls.append(
            {
                "corporation_id": corp_id,
                "corporation_name": corp_name,
                "share_scope": share_scope,
                "badge_class": state.get(
                    "badge_class", default_state.get("badge_class")
                ),
                "status_label": state.get(
                    "status_label", default_state.get("status_label")
                ),
                "status_hint": state.get(
                    "status_hint", default_state.get("status_hint")
                ),
                "has_blueprint_scope": bool(
                    entry.get("blueprint", {}).get("has_scope")
                ),
                "blueprint_character": entry.get("blueprint", {}).get("character_name"),
                "has_jobs_scope": bool(entry.get("jobs", {}).get("has_scope")),
                "jobs_character": entry.get("jobs", {}).get("character_name"),
                "requires_manual_authorization": entry.get("authorization", {}).get(
                    "restricted", False
                ),
                "authorized_characters": entry.get("authorization", {}).get(
                    "characters", []
                ),
            }
        )

    summary = {
        "total": len(controls),
        "enabled": sum(
            1
            for item in controls
            if item["share_scope"] != CharacterSettings.SCOPE_NONE
        ),
    }
    return controls, summary


def _describe_job_notification_hint(frequency: str, custom_days: int) -> str:
    custom_days = max(1, custom_days or 1)
    hints = {
        CharacterSettings.NOTIFY_DISABLED: _(
            "Muted: we stay quiet until you re-enable alerts."
        ),
        CharacterSettings.NOTIFY_IMMEDIATE: _(
            "Instant: we ping you the moment a job completes."
        ),
        CharacterSettings.NOTIFY_DAILY: _("Daily digest: one summary every night."),
        CharacterSettings.NOTIFY_WEEKLY: _("Weekly digest: recap every seven days."),
        CharacterSettings.NOTIFY_MONTHLY: _(
            "Monthly digest: grouped update every thirty days."
        ),
        CharacterSettings.NOTIFY_CUSTOM: _(
            "Custom cadence: grouped alert every %(days)s day(s)."
        )
        % {"days": custom_days},
    }
    return hints.get(frequency, hints[CharacterSettings.NOTIFY_DISABLED])


def get_copy_sharing_states() -> dict[str, dict[str, object]]:
    return {
        CharacterSettings.SCOPE_NONE: {
            "enabled": False,
            "button_label": _("Private"),
            "button_hint": _("Your originals stay private for now."),
            "status_label": _("Sharing disabled"),
            "status_hint": _(
                "Blueprint requests stay hidden until you enable sharing."
            ),
            "badge_class": "bg-secondary-subtle text-secondary",
            "popup_message": _("Blueprint sharing disabled."),
            "fulfill_hint": _(
                "Enable sharing to see requests that match your originals."
            ),
            "subtitle": _(
                "Keep your library private until you're ready to collaborate."
            ),
            "explanation": _(
                "Only you can view or request copies; other pilots cannot see your originals."
            ),
        },
        CharacterSettings.SCOPE_CORPORATION: {
            "enabled": True,
            "button_label": _("Corporation"),
            "button_hint": _("Corpmates can request copies of your originals."),
            "status_label": _("Shared with corporation"),
            "status_hint": _("Blueprint requests are visible to your corporation."),
            "badge_class": "bg-warning-subtle text-warning",
            "popup_message": _("Blueprint sharing enabled for your corporation."),
            "fulfill_hint": _("Corporation pilots may be waiting on your copies."),
            "subtitle": _("Share duplicates with trusted corp industrialists."),
            "explanation": _(
                "Pilots in your corporation can see your originals and submit copy requests."
            ),
        },
        CharacterSettings.SCOPE_ALLIANCE: {
            "enabled": True,
            "button_label": _("Alliance"),
            "button_hint": _("Alliance pilots can request copies of your originals."),
            "status_label": _("Shared with alliance"),
            "status_hint": _("Blueprint requests are visible to your alliance."),
            "badge_class": "bg-primary-subtle text-primary",
            "popup_message": _("Blueprint sharing enabled for the entire alliance."),
            "fulfill_hint": _("Alliance pilots may be waiting on you."),
            "subtitle": _("Coordinate duplicate production across your alliance."),
            "explanation": _(
                "Everyone in your alliance can browse your originals and ask for copies."
            ),
        },
        CharacterSettings.SCOPE_EVERYONE: {
            "enabled": True,
            "button_label": _("Everyone"),
            "button_hint": _(
                "All pilots with copy permissions can request your originals."
            ),
            "status_label": _("Shared with everyone"),
            "status_hint": _(
                "Blueprint requests are visible to all authorized Indy Hub users."
            ),
            "badge_class": "bg-success-subtle text-success",
            "popup_message": _("Blueprint sharing enabled for everyone."),
            "fulfill_hint": _(
                "Any pilot with copy privileges may be waiting on your response."
            ),
            "subtitle": _(
                "Open your originals to the wider alliance community who can share copies."
            ),
            "explanation": _(
                "Any Indy Hub pilot with the copy permission can see your originals and request copies."
            ),
        },
    }


# --- User views (token management, sync, etc.) ---
def _build_dashboard_context(request):
    """Collect shared dashboard context for Indy Hub dashboards."""

    blueprint_char_ids: list[int] = []
    jobs_char_ids: list[int] = []

    if Token:
        try:
            blueprint_char_ids = list(
                Token.objects.filter(user=request.user)
                .require_scopes(BLUEPRINT_SCOPE_SET)
                .values_list("character_id", flat=True)
                .distinct()
            )
            jobs_char_ids = list(
                Token.objects.filter(user=request.user)
                .require_scopes(JOBS_SCOPE_SET)
                .values_list("character_id", flat=True)
                .distinct()
            )
        except Exception:
            blueprint_char_ids = jobs_char_ids = []

    blueprint_char_id_set = set(blueprint_char_ids)
    jobs_char_id_set = set(jobs_char_ids)

    user_chars = []
    ownerships = CharacterOwnership.objects.filter(user=request.user)
    for ownership in ownerships:
        cid = ownership.character.character_id
        user_chars.append(
            {
                "character_id": cid,
                "name": get_character_name(cid),
                "bp_enabled": cid in blueprint_char_id_set,
                "jobs_enabled": cid in jobs_char_id_set,
            }
        )

    blueprints_qs = Blueprint.objects.filter(owner_user=request.user)

    def normalized_quantity(value: int | None) -> int:
        if value in (-1, -2):
            return 1
        if value is None:
            return 0
        return max(value, 0)

    blueprint_count = 0
    original_blueprints = 0
    copy_blueprints = 0

    for bp in blueprints_qs:
        qty = normalized_quantity(bp.quantity)
        blueprint_count += qty
        if bp.is_copy:
            copy_blueprints += qty
        else:
            original_blueprints += qty

    jobs_qs = IndustryJob.objects.filter(owner_user=request.user)
    now = timezone.now()
    today = now.date()
    active_jobs_count = jobs_qs.filter(status="active", end_date__gt=now).count()
    completed_jobs_count = jobs_qs.filter(end_date__lte=now).count()
    completed_jobs_today = jobs_qs.filter(
        end_date__date=today, end_date__lte=now
    ).count()

    settings_obj, _created = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )

    pending_updates: list[str] = []
    if not settings_obj.jobs_notify_frequency:
        default_frequency = (
            CharacterSettings.NOTIFY_IMMEDIATE
            if settings_obj.jobs_notify_completed
            else CharacterSettings.NOTIFY_DISABLED
        )
        settings_obj.jobs_notify_frequency = default_frequency
        pending_updates.append("jobs_notify_frequency")

    if (
        not settings_obj.jobs_notify_custom_days
        or settings_obj.jobs_notify_custom_days < 1
    ):
        settings_obj.jobs_notify_custom_days = 3
        pending_updates.append("jobs_notify_custom_days")

    if pending_updates:
        settings_obj.save(update_fields=pending_updates)

    jobs_notify_frequency = settings_obj.jobs_notify_frequency
    valid_frequencies = dict(CharacterSettings.JOB_NOTIFICATION_FREQUENCY_CHOICES)
    if jobs_notify_frequency not in valid_frequencies:
        jobs_notify_frequency = CharacterSettings.NOTIFY_DISABLED
        if settings_obj.jobs_notify_frequency != jobs_notify_frequency:
            settings_obj.jobs_notify_frequency = jobs_notify_frequency
            settings_obj.save(update_fields=["jobs_notify_frequency"])

    jobs_notify_completed = jobs_notify_frequency != CharacterSettings.NOTIFY_DISABLED
    if settings_obj.jobs_notify_completed != jobs_notify_completed:
        settings_obj.jobs_notify_completed = jobs_notify_completed
        settings_obj.save(update_fields=["jobs_notify_completed"])

    job_notification_custom_days = max(1, settings_obj.jobs_notify_custom_days or 1)
    if job_notification_custom_days != settings_obj.jobs_notify_custom_days:
        settings_obj.jobs_notify_custom_days = job_notification_custom_days
        settings_obj.save(update_fields=["jobs_notify_custom_days"])

    job_notification_hint = _describe_job_notification_hint(
        jobs_notify_frequency, job_notification_custom_days
    )
    copy_sharing_scope = settings_obj.copy_sharing_scope
    if copy_sharing_scope not in dict(CharacterSettings.COPY_SHARING_SCOPE_CHOICES):
        copy_sharing_scope = CharacterSettings.SCOPE_NONE

    copy_sharing_states = get_copy_sharing_states()
    copy_sharing_states_with_scope = {
        key: {**value, "scope": key} for key, value in copy_sharing_states.items()
    }
    sharing_state = copy_sharing_states.get(
        copy_sharing_scope, copy_sharing_states[CharacterSettings.SCOPE_NONE]
    )

    allow_copy_requests = sharing_state["enabled"]
    if allow_copy_requests != settings_obj.allow_copy_requests:
        settings_obj.allow_copy_requests = allow_copy_requests
        settings_obj.save(update_fields=["allow_copy_requests"])

    copy_fulfill_count = 0

    my_open_requests_qs = BlueprintCopyRequest.objects.filter(
        requested_by=request.user, fulfilled=False
    )
    copy_my_requests_open = my_open_requests_qs.count()

    copy_my_requests_pending_delivery = BlueprintCopyRequest.objects.filter(
        requested_by=request.user,
        fulfilled=True,
        delivered=False,
    ).count()

    if sharing_state["enabled"]:
        fulfill_filters = Q()
        originals_for_fulfill = blueprints_qs.filter(
            bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION]
        )

        for bp in originals_for_fulfill:
            fulfill_filters |= Q(
                type_id=bp.type_id,
                material_efficiency=bp.material_efficiency,
                time_efficiency=bp.time_efficiency,
            )

        if fulfill_filters:
            fulfill_qs = BlueprintCopyRequest.objects.filter(fulfill_filters)
            fulfill_qs = fulfill_qs.filter(
                Q(fulfilled=False)
                | Q(
                    fulfilled=True,
                    delivered=False,
                    offers__owner=request.user,
                )
            ).exclude(requested_by=request.user)

            fulfill_qs = fulfill_qs.exclude(
                offers__owner=request.user, offers__status="rejected"
            )

            copy_fulfill_count = fulfill_qs.distinct().count()

    copy_my_requests_total = copy_my_requests_open + copy_my_requests_pending_delivery

    unread_chats_base = BlueprintCopyChat.objects.filter(
        is_open=True,
        last_message_at__isnull=False,
    ).filter(
        (
            Q(buyer=request.user, last_message_role="seller")
            & (
                Q(buyer_last_seen_at__isnull=True)
                | Q(buyer_last_seen_at__lt=F("last_message_at"))
            )
        )
        | (
            Q(seller=request.user, last_message_role="buyer")
            & (
                Q(seller_last_seen_at__isnull=True)
                | Q(seller_last_seen_at__lt=F("last_message_at"))
            )
        )
    )

    copy_chat_unread_count = unread_chats_base.count()
    unread_chat_cards = list(
        unread_chats_base.select_related("request", "offer").order_by(
            "-last_message_at"
        )[:5]
    )

    copy_chat_alerts: list[dict[str, Any]] = []
    for chat in unread_chat_cards:
        request_obj = chat.request
        viewer_role = "buyer" if chat.buyer_id == request.user.id else "seller"
        other_label = _("Builder") if viewer_role == "buyer" else _("Buyer")
        last_message_local = timezone.localtime(chat.last_message_at)
        copy_chat_alerts.append(
            {
                "chat_id": chat.id,
                "type_id": request_obj.type_id,
                "type_name": get_type_name(request_obj.type_id),
                "viewer_role": viewer_role,
                "fetch_url": reverse("indy_hub:bp_chat_history", args=[chat.id]),
                "send_url": reverse("indy_hub:bp_chat_send", args=[chat.id]),
                "source_url": reverse(
                    "indy_hub:bp_copy_my_requests"
                    if viewer_role == "buyer"
                    else "indy_hub:bp_copy_fulfill_requests"
                ),
                "source_label": (
                    _("View my requests")
                    if viewer_role == "buyer"
                    else _("Open fulfill queue")
                ),
                "other_label": other_label,
                "last_message_at": last_message_local,
                "last_message_display": last_message_local.strftime("%Y-%m-%d %H:%M"),
            }
        )

    onboarding_progress, _created = UserOnboardingProgress.objects.get_or_create(
        user=request.user
    )
    manual_steps = onboarding_progress.manual_steps or {}
    has_any_request_history = BlueprintCopyRequest.objects.filter(
        requested_by=request.user
    ).exists()

    onboarding_tasks = []
    for cfg in ONBOARDING_TASK_CONFIG:
        task = {
            "key": cfg["key"],
            "title": cfg["title"],
            "description": cfg["description"],
            "mode": cfg["mode"],
            "icon": cfg.get("icon"),
            "cta": cfg.get("cta"),
        }
        if cfg["mode"] == "manual":
            completed = bool(manual_steps.get(cfg["key"]))
        else:
            if cfg["key"] == "connect_blueprints":
                completed = bool(blueprint_char_ids)
            elif cfg["key"] == "connect_jobs":
                completed = bool(jobs_char_ids)
            elif cfg["key"] == "enable_sharing":
                completed = bool(sharing_state["enabled"])
            elif cfg["key"] == "submit_request":
                completed = has_any_request_history
            else:
                completed = False
        task["completed"] = completed
        cta_name = task.get("cta")
        if cta_name:
            try:
                task["cta_url"] = reverse(cta_name)
            except Exception:
                task["cta_url"] = None
        else:
            task["cta_url"] = None
        onboarding_tasks.append(task)

    completed_count = sum(1 for task in onboarding_tasks if task["completed"])
    total_tasks = len(onboarding_tasks)
    pending_tasks = [task for task in onboarding_tasks if not task["completed"]]
    onboarding_percent = (
        int(round((completed_count / total_tasks) * 100)) if total_tasks else 0
    )
    onboarding_show = bool(pending_tasks) and not onboarding_progress.dismissed

    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corporate_assets")
    corp_scope_status = _collect_corporation_scope_status(request.user)
    corporation_share_controls, corporation_share_summary = (
        _build_corporation_share_controls(request.user, corp_scope_status)
    )
    corporation_share_controls_json = json.dumps(corporation_share_controls)
    corp_blueprint_scope_count = sum(
        1 for status in corp_scope_status if status["blueprint"]["has_scope"]
    )
    corp_jobs_scope_count = sum(
        1 for status in corp_scope_status if status["jobs"]["has_scope"]
    )

    corporation_overview = build_corporation_sharing_context(request.user)
    corp_blueprint_count = 0
    corp_original_blueprints = 0
    corp_copy_blueprints = 0
    corp_reaction_blueprints = 0
    corp_jobs_total = 0
    corp_active_jobs_count = 0
    corp_jobs_completed = 0

    if corporation_overview:
        corp_blueprint_count = corporation_overview.get("total_blueprints", 0) or 0
        corp_jobs_total = corporation_overview.get("total_jobs", 0) or 0
        for corp_entry in corporation_overview.get("corporations", []):
            blueprints = corp_entry.get("blueprints", {}) or {}
            corp_original_blueprints += blueprints.get("originals", 0) or 0
            corp_copy_blueprints += blueprints.get("copies", 0) or 0
            corp_reaction_blueprints += blueprints.get("reactions", 0) or 0

            jobs = corp_entry.get("jobs", {}) or {}
            corp_active_jobs_count += jobs.get("active", 0) or 0
            corp_jobs_completed += jobs.get("completed", 0) or 0

    context = {
        "has_blueprint_tokens": bool(blueprint_char_ids),
        "has_jobs_tokens": bool(jobs_char_ids),
        "blueprint_token_count": len(blueprint_char_ids),
        "jobs_token_count": len(jobs_char_ids),
        "characters": user_chars,
        "blueprint_count": blueprint_count,
        "original_blueprints": original_blueprints,
        "copy_blueprints": copy_blueprints,
        "active_jobs_count": active_jobs_count,
        "completed_jobs_count": completed_jobs_count,
        "completed_jobs_today": completed_jobs_today,
        "jobs_notify_completed": jobs_notify_completed,
        "job_notification_frequency": jobs_notify_frequency,
        "job_notification_custom_days": job_notification_custom_days,
        "job_notification_hint": job_notification_hint,
        "job_notification_next_digest": settings_obj.jobs_next_digest_at,
        "allow_copy_requests": sharing_state["enabled"],
        "copy_sharing_scope": copy_sharing_scope,
        "copy_sharing_state": sharing_state,
        "copy_sharing_states_json": json.dumps(copy_sharing_states_with_scope),
        "copy_fulfill_count": copy_fulfill_count,
        "copy_my_requests_open": copy_my_requests_open,
        "copy_my_requests_pending_delivery": copy_my_requests_pending_delivery,
        "copy_my_requests_total": copy_my_requests_total,
        "copy_chat_unread_count": copy_chat_unread_count,
        "copy_chat_alerts": copy_chat_alerts,
        "copy_chat_alerts_has_more": copy_chat_unread_count > len(copy_chat_alerts),
        "onboarding": {
            "tasks": onboarding_tasks,
            "completed": completed_count,
            "total": total_tasks,
            "pending": len(pending_tasks),
            "percent": onboarding_percent,
            "show": onboarding_show,
            "dismissed": onboarding_progress.dismissed,
        },
        "can_manage_corporate_assets": can_manage_corp,
        "has_corp_blueprint_tokens": corp_blueprint_scope_count > 0,
        "has_corp_job_tokens": corp_jobs_scope_count > 0,
        "corporation_share_controls": corporation_share_controls,
        "corporation_share_controls_json": corporation_share_controls_json,
        "corporation_share_summary": corporation_share_summary,
        "corporation_overview": corporation_overview,
        "corp_blueprint_count": corp_blueprint_count,
        "corp_original_blueprints": corp_original_blueprints,
        "corp_copy_blueprints": corp_copy_blueprints,
        "corp_reaction_blueprints": corp_reaction_blueprints,
        "corp_jobs_total": corp_jobs_total,
        "corp_active_jobs_count": corp_active_jobs_count,
        "corp_jobs_completed": corp_jobs_completed,
        "corp_scope_status": corp_scope_status,
        "show_corporation_tab": can_manage_corp,
    }
    return context


@indy_hub_access_required
@login_required
def index(request):
    context = _build_dashboard_context(request)
    personal_url = reverse("indy_hub:index")
    corporation_url = (
        reverse("indy_hub:corporation_dashboard")
        if context.get("show_corporation_tab")
        else None
    )
    context.update(
        {
            "personal_nav_url": personal_url,
            "personal_nav_class": "active fw-semibold",
            "corporation_nav_url": corporation_url,
            "corporation_nav_class": "",
            "current_dashboard": "personal",
        }
    )
    return render(request, "indy_hub/index.html", context)


@indy_hub_access_required
@login_required
def corporation_dashboard(request):
    context = _build_dashboard_context(request)
    if not context.get("show_corporation_tab"):
        raise PermissionDenied
    context.update(
        {
            "personal_nav_url": reverse("indy_hub:index"),
            "personal_nav_class": "",
            "corporation_nav_url": reverse("indy_hub:corporation_dashboard"),
            "corporation_nav_class": "active fw-semibold",
            "current_dashboard": "corporation",
        }
    )
    return render(request, "indy_hub/corporation_dashboard.html", context)


@indy_hub_access_required
@login_required
def token_management(request):
    blueprint_tokens = None
    jobs_tokens = None
    (
        corp_scope_status,
        corp_scope_warnings,
    ) = _collect_corporation_scope_status(request.user, include_warnings=True)
    corp_scope_status = [
        status
        for status in corp_scope_status
        if status.get("blueprint", {}).get("has_scope")
        or status.get("jobs", {}).get("has_scope")
    ]
    corporation_sharing = build_corporation_sharing_context(request.user)
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corporate_assets")
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                BLUEPRINT_SCOPE_SET
            )
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                JOBS_SCOPE_SET
            )
            # Deduplicate by character_id
            blueprint_char_ids = (
                list(blueprint_tokens.values_list("character_id", flat=True).distinct())
                if blueprint_tokens
                else []
            )
            jobs_char_ids = (
                list(jobs_tokens.values_list("character_id", flat=True).distinct())
                if jobs_tokens
                else []
            )
        except Exception:
            blueprint_tokens = jobs_tokens = None
            blueprint_char_ids = jobs_char_ids = []
    blueprint_auth_url = (
        reverse("indy_hub:authorize_blueprints") if CallbackRedirect else None
    )
    jobs_auth_url = reverse("indy_hub:authorize_jobs") if CallbackRedirect else None
    corp_blueprint_auth_url = (
        reverse("indy_hub:authorize_corp_blueprints")
        if can_manage_corp and CallbackRedirect
        else None
    )
    corp_jobs_auth_url = (
        reverse("indy_hub:authorize_corp_jobs")
        if can_manage_corp and CallbackRedirect
        else None
    )
    corp_all_auth_url = (
        reverse("indy_hub:authorize_corp_all")
        if can_manage_corp and CallbackRedirect
        else None
    )
    user_chars = []
    ownerships = CharacterOwnership.objects.filter(user=request.user)
    for ownership in ownerships:
        cid = ownership.character.character_id
        user_chars.append(
            {
                "character_id": cid,
                "name": get_character_name(cid),
                "bp_enabled": (
                    blueprint_tokens.filter(character_id=cid).exists()
                    if blueprint_tokens
                    else False
                ),
                "jobs_enabled": (
                    jobs_tokens.filter(character_id=cid).exists()
                    if jobs_tokens
                    else False
                ),
            }
        )

    warning_payload: list[dict[str, str]] = []
    if corp_scope_warnings:
        seen_messages: set[str] = set()
        for warning in corp_scope_warnings:
            reason = warning.get("reason")
            corp_name = (
                warning.get("corporation_name")
                or get_corporation_name(warning.get("corporation_id"))
                or str(warning.get("corporation_id"))
            )
            character_name = (
                warning.get("character_name")
                or get_character_name(warning.get("character_id"))
                or str(warning.get("character_id"))
            )

            if reason == "missing_roles_scope":
                message = _(
                    "%(character)s must authorize the corporation roles scope before Indy Hub can use %(corporation)s director tokens."
                ) % {
                    "character": character_name,
                    "corporation": corp_name,
                }
                if warning.get("tokens_revoked"):
                    message += " " + _("Indy Hub removed the unusable tokens.")
                level = "warning"
            elif reason == "missing_required_roles":
                required_roles = warning.get("required_roles") or sorted(
                    REQUIRED_CORPORATION_ROLES
                )
                message = _(
                    "%(character)s lacks the required corporation roles (%(roles)s) for %(corporation)s."
                ) % {
                    "character": character_name,
                    "roles": ", ".join(required_roles),
                    "corporation": corp_name,
                }
                if warning.get("tokens_revoked"):
                    message += " " + _("Indy Hub removed the unusable tokens.")
                else:
                    message += " " + _("Tokens remain restricted.")
                level = "danger"
            else:
                continue

            if message in seen_messages:
                continue
            seen_messages.add(message)
            warning_payload.append({"message": message, "level": level})

    warning_payload_json = json.dumps(warning_payload) if warning_payload else ""
    context = {
        "has_blueprint_tokens": bool(blueprint_char_ids),
        "has_jobs_tokens": bool(jobs_char_ids),
        "blueprint_token_count": len(blueprint_char_ids),
        "jobs_token_count": len(jobs_char_ids),
        "blueprint_auth_url": blueprint_auth_url,
        "jobs_auth_url": jobs_auth_url,
        "characters": user_chars,
        "can_manage_corporate_assets": can_manage_corp,
        "corporation_sharing": corporation_sharing,
        "corporations": corp_scope_status,
        "corp_blueprint_auth_url": corp_blueprint_auth_url,
        "corp_jobs_auth_url": corp_jobs_auth_url,
        "corp_all_auth_url": corp_all_auth_url,
        "corp_count": len(corp_scope_status),
        "corp_blueprint_scope_count": sum(
            1 for status in corp_scope_status if status["blueprint"]["has_scope"]
        ),
        "corp_jobs_scope_count": sum(
            1 for status in corp_scope_status if status["jobs"]["has_scope"]
        ),
        "corp_role_warning_payload_json": warning_payload_json,
        "corp_role_warning_count": len(warning_payload),
    }
    context.update(
        build_nav_context(
            request.user, can_manage_corp=can_manage_corp, active_tab="personal"
        )
    )
    return render(request, "indy_hub/token_management.html", context)


@indy_hub_access_required
@login_required
def authorize_blueprints(request):
    # Only skip if ALL characters are already authorized for blueprint scope
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    authorized = (
        Token.objects.filter(user=request.user)
        .require_scopes(BLUEPRINT_SCOPE_SET)
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - set(authorized)
    if not missing:
        messages.info(request, "All characters already have blueprint access.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        blueprint_state = f"indy_hub_blueprints_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=blueprint_state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        blueprint_params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(BLUEPRINT_SCOPE_SET),
            "state": blueprint_state,
        }
        blueprint_auth_url = f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(blueprint_params)}"
        return redirect(blueprint_auth_url)
    except Exception as e:
        logger.error(f"Error creating blueprint authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_jobs(request):
    # Only skip if ALL characters have jobs access
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    authorized = (
        Token.objects.filter(user=request.user)
        .require_scopes(JOBS_SCOPE_SET)
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - set(authorized)
    if not missing:
        messages.info(request, "All characters already have jobs access.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        jobs_state = f"indy_hub_jobs_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=jobs_state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        jobs_params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(JOBS_SCOPE_SET),
            "state": jobs_state,
        }
        jobs_auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(jobs_params)}"
        )
        return redirect(jobs_auth_url)
    except Exception as e:
        logger.error(f"Error creating jobs authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_corp_blueprints(request):
    if not request.user.has_perm("indy_hub.can_manage_corporate_assets"):
        messages.error(
            request, "You do not have permission to manage corporation assets."
        )
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        state = f"indy_hub_corp_blueprints_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(sorted(set(CORP_BLUEPRINT_SCOPE_SET))),
            "state": state,
        }
        auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(params)}"
        )
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error creating corporation blueprint authorization: {e}")
        messages.error(request, f"Error setting up corporation authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_corp_jobs(request):
    if not request.user.has_perm("indy_hub.can_manage_corporate_assets"):
        messages.error(
            request, "You do not have permission to manage corporation assets."
        )
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        state = f"indy_hub_corp_jobs_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(sorted(set(CORP_JOBS_SCOPE_SET))),
            "state": state,
        }
        auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(params)}"
        )
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error creating corporation job authorization: {e}")
        messages.error(request, f"Error setting up corporation authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_corp_all(request):
    if not request.user.has_perm("indy_hub.can_manage_corporate_assets"):
        messages.error(
            request, "You do not have permission to manage corporation assets."
        )
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        state = f"indy_hub_corp_all_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        scope_set = sorted(
            {
                *CORP_BLUEPRINT_SCOPE_SET,
                *CORP_JOBS_SCOPE_SET,
            }
        )
        params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(scope_set),
            "state": state,
        }
        auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(params)}"
        )
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error creating corporation authorization: {e}")
        messages.error(request, f"Error setting up corporation authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_all(request):
    # Only skip if ALL characters have both blueprint and jobs access
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    blueprint_auth = (
        Token.objects.filter(user=request.user)
        .require_scopes(BLUEPRINT_SCOPE_SET)
        .values_list("character_id", flat=True)
    )
    jobs_auth = (
        Token.objects.filter(user=request.user)
        .require_scopes(JOBS_SCOPE_SET)
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - (set(blueprint_auth) & set(jobs_auth))
    if not missing:
        messages.info(request, "All characters already authorized for all scopes.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        state = f"indy_hub_all_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        combined_scopes = sorted({*BLUEPRINT_SCOPE_SET, *JOBS_SCOPE_SET})
        params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": " ".join(combined_scopes),
            "state": state,
        }
        auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(params)}"
        )
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error creating combined authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_all_tokens(request):
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                BLUEPRINT_SCOPE_SET
            )
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                JOBS_SCOPE_SET
            )
            any_scheduled = False
            if blueprint_tokens.exists():
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_BLUEPRINTS,
                    request.user.id,
                    priority=5,
                )
                if scheduled:
                    any_scheduled = True
                    messages.success(
                        request,
                        _("Blueprint synchronization scheduled."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Blueprint synchronization is on cooldown. Please retry in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
            else:
                messages.warning(
                    request,
                    _("No blueprint tokens available for synchronization."),
                )

            if jobs_tokens.exists():
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_JOBS,
                    request.user.id,
                    priority=5,
                )
                if scheduled:
                    any_scheduled = True
                    messages.success(
                        request,
                        _("Industry jobs synchronization scheduled."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Jobs synchronization is on cooldown. Please retry in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
            else:
                messages.warning(
                    request,
                    _("No jobs tokens available for synchronization."),
                )

            if not any_scheduled:
                logger.info(
                    "User %s requested sync_all_tokens but no tasks were queued due to cooldown or missing tokens",
                    request.user.username,
                )
        except Exception as e:
            logger.error(f"Error triggering sync_all: {e}")
            messages.error(request, "Error starting synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_blueprints(request):
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                BLUEPRINT_SCOPE_SET
            )
            if blueprint_tokens.exists():
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_BLUEPRINTS,
                    request.user.id,
                    priority=5,
                )
                if scheduled:
                    messages.success(
                        request,
                        _("Blueprint synchronization scheduled."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Blueprint synchronization is on cooldown. Please retry in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
            else:
                messages.warning(
                    request, "No blueprint tokens available for synchronization."
                )
        except Exception as e:
            logger.error(f"Error triggering sync_blueprints: {e}")
            messages.error(request, "Error starting blueprint synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_jobs(request):
    if Token:
        try:
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                JOBS_SCOPE_SET
            )
            if jobs_tokens.exists():
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_JOBS,
                    request.user.id,
                    priority=5,
                )
                if scheduled:
                    messages.success(
                        request,
                        _("Jobs synchronization scheduled."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Jobs synchronization is on cooldown. Please retry in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
            else:
                messages.warning(
                    request, "No jobs tokens available for synchronization."
                )
        except Exception as e:
            logger.error(f"Error triggering sync_jobs: {e}")
            messages.error(request, "Error starting jobs synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


# Toggle notification des travaux
@indy_hub_access_required
@login_required
@require_POST
def toggle_job_notifications(request):
    payload: dict[str, object] = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return JsonResponse({"error": "invalid_payload"}, status=400)

    settings, _created = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )

    frequency = None
    custom_days = None
    if isinstance(payload, dict):
        frequency = payload.get("frequency")
        custom_days = payload.get("custom_days")

    valid_frequencies = dict(CharacterSettings.JOB_NOTIFICATION_FREQUENCY_CHOICES)

    if frequency not in valid_frequencies:
        # Legacy fallback: toggle simple on/off when no explicit frequency supplied.
        settings.jobs_notify_completed = not settings.jobs_notify_completed
        if settings.jobs_notify_completed:
            frequency = (
                settings.jobs_notify_frequency
                if settings.jobs_notify_frequency
                and settings.jobs_notify_frequency != CharacterSettings.NOTIFY_DISABLED
                else CharacterSettings.NOTIFY_IMMEDIATE
            )
        else:
            frequency = CharacterSettings.NOTIFY_DISABLED
        settings.set_job_notification_frequency(frequency)
    else:
        days_value = None
        if frequency == CharacterSettings.NOTIFY_CUSTOM:
            if custom_days is None:
                custom_days = settings.jobs_notify_custom_days
            try:
                days_value = int(custom_days)
            except (TypeError, ValueError):
                return JsonResponse({"error": "invalid_custom_days"}, status=400)
            if days_value < 1 or days_value > 365:
                return JsonResponse({"error": "invalid_custom_days"}, status=400)
        settings.set_job_notification_frequency(frequency, custom_days=days_value)

    if frequency in {
        CharacterSettings.NOTIFY_DAILY,
        CharacterSettings.NOTIFY_WEEKLY,
        CharacterSettings.NOTIFY_MONTHLY,
        CharacterSettings.NOTIFY_CUSTOM,
    }:
        settings.schedule_next_digest()
    else:
        settings.jobs_next_digest_at = None

    settings.save(
        update_fields=[
            "jobs_notify_frequency",
            "jobs_notify_custom_days",
            "jobs_notify_completed",
            "jobs_next_digest_at",
            "updated_at",
        ]
    )

    hint = _describe_job_notification_hint(
        settings.jobs_notify_frequency, settings.jobs_notify_custom_days
    )

    message_map = {
        CharacterSettings.NOTIFY_DISABLED: _("Industry job alerts muted."),
        CharacterSettings.NOTIFY_IMMEDIATE: _("You'll receive live job alerts."),
        CharacterSettings.NOTIFY_DAILY: _("Daily job digest enabled."),
        CharacterSettings.NOTIFY_WEEKLY: _("Weekly job digest enabled."),
        CharacterSettings.NOTIFY_MONTHLY: _("Monthly job digest enabled."),
        CharacterSettings.NOTIFY_CUSTOM: _("Custom job digest every %(days)s day(s).")
        % {"days": settings.jobs_notify_custom_days},
    }

    response_payload = {
        "frequency": settings.jobs_notify_frequency,
        "custom_days": settings.jobs_notify_custom_days,
        "hint": hint,
        "message": message_map.get(
            settings.jobs_notify_frequency,
            _("Job notification preferences updated."),
        ),
        "enabled": settings.jobs_notify_completed,
    }
    if settings.jobs_next_digest_at:
        response_payload["next_digest_at"] = settings.jobs_next_digest_at.isoformat()

    return JsonResponse(response_payload)


# Toggle pooling de partage de copies
@indy_hub_access_required
@login_required
@require_POST
def toggle_copy_sharing(request):
    settings, _created = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )
    scope_order = [
        CharacterSettings.SCOPE_NONE,
        CharacterSettings.SCOPE_CORPORATION,
        CharacterSettings.SCOPE_ALLIANCE,
        CharacterSettings.SCOPE_EVERYONE,
    ]
    payload = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            payload = {}

    requested_scope = payload.get("scope") if isinstance(payload, dict) else None
    if requested_scope in scope_order:
        next_scope = requested_scope
    else:
        try:
            current_index = scope_order.index(settings.copy_sharing_scope)
        except ValueError:
            current_index = 0
        next_scope = scope_order[(current_index + 1) % len(scope_order)]

    settings.set_copy_sharing_scope(next_scope)
    settings.save(
        update_fields=["allow_copy_requests", "copy_sharing_scope", "updated_at"]
    )

    sharing_state = get_copy_sharing_states()[next_scope]

    return JsonResponse(
        {
            "scope": next_scope,
            "enabled": sharing_state["enabled"],
            "button_label": sharing_state["button_label"],
            "button_hint": sharing_state["button_hint"],
            "status_label": sharing_state["status_label"],
            "status_hint": sharing_state["status_hint"],
            "badge_class": sharing_state["badge_class"],
            "popup_message": sharing_state["popup_message"],
            "fulfill_hint": sharing_state["fulfill_hint"],
            "subtitle": sharing_state["subtitle"],
        }
    )


@indy_hub_access_required
@login_required
@require_POST
def toggle_corporation_copy_sharing(request):
    if not request.user.has_perm("indy_hub.can_manage_corporate_assets"):
        return JsonResponse({"error": "forbidden"}, status=403)

    payload = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return JsonResponse({"error": "invalid_payload"}, status=400)

    corp_id = payload.get("corporation_id")
    scope = payload.get("scope")

    try:
        corp_id = int(corp_id)
    except (TypeError, ValueError):
        corp_id = None

    valid_scopes = dict(CharacterSettings.COPY_SHARING_SCOPE_CHOICES)
    if not corp_id:
        return JsonResponse({"error": "invalid_corporation"}, status=400)
    if scope not in valid_scopes:
        return JsonResponse({"error": "invalid_scope"}, status=400)

    corp_scope_status = _collect_corporation_scope_status(request.user)
    corp_entry = next(
        (
            entry
            for entry in corp_scope_status
            if entry.get("corporation_id") == corp_id
        ),
        None,
    )
    if not corp_entry:
        return JsonResponse({"error": "unknown_corporation"}, status=404)

    corp_name = corp_entry.get("corporation_name") or str(corp_id)
    setting, _created = CorporationSharingSetting.objects.get_or_create(
        user=request.user,
        corporation_id=corp_id,
        defaults={
            "corporation_name": corp_name,
            "share_scope": CharacterSettings.SCOPE_NONE,
            "allow_copy_requests": False,
        },
    )
    setting.corporation_name = corp_name
    setting.set_share_scope(scope)
    setting.save(
        update_fields=[
            "corporation_name",
            "share_scope",
            "allow_copy_requests",
            "updated_at",
        ]
    )

    sharing_states = get_copy_sharing_states()
    state = dict(
        sharing_states.get(scope, sharing_states[CharacterSettings.SCOPE_NONE])
    )

    base_popup = state.get("popup_message") or _("Blueprint sharing updated.")
    state["popup_message"] = _("%(corp)s: %(message)s") % {
        "corp": corp_name,
        "message": base_popup,
    }

    response_payload = {
        "corporation_id": corp_id,
        "corporation_name": corp_name,
        "scope": scope,
        "enabled": state.get("enabled", False),
        "badge_class": state.get("badge_class", "bg-secondary-subtle text-secondary"),
        "status_label": state.get("status_label", _("Sharing disabled")),
        "status_hint": state.get(
            "status_hint",
            _("Blueprint requests stay hidden until you enable sharing."),
        ),
        "button_hint": state.get("button_hint", ""),
        "popup_message": state.get("popup_message"),
    }
    return JsonResponse(response_payload)


@indy_hub_access_required
@login_required
@require_POST
def onboarding_toggle_task(request):
    task_key = request.POST.get("task", "").strip()
    action = request.POST.get("action", "complete")
    next_url = (
        request.POST.get("next")
        or request.headers.get("referer")
        or reverse("indy_hub:index")
    )
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("indy_hub:index")

    if task_key not in MANUAL_ONBOARDING_KEYS:
        messages.error(request, _("This checklist item can't be updated manually."))
        return redirect(next_url)

    progress, _created = UserOnboardingProgress.objects.get_or_create(user=request.user)
    completed = action != "reset"
    progress.mark_step(task_key, completed)
    fields = ["manual_steps", "updated_at"]
    if completed and progress.dismissed:
        progress.dismissed = False
        fields.append("dismissed")
    progress.save(update_fields=list(dict.fromkeys(fields)))

    if completed:
        messages.success(
            request, _("Nice! We'll remember that you've reviewed the guides.")
        )
    else:
        messages.info(request, _("Checklist item reset."))
    return redirect(next_url)


@indy_hub_access_required
@login_required
@require_POST
def onboarding_set_visibility(request):
    action = request.POST.get("action", "dismiss")
    next_url = (
        request.POST.get("next")
        or request.headers.get("referer")
        or reverse("indy_hub:index")
    )
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("indy_hub:index")

    progress, _created = UserOnboardingProgress.objects.get_or_create(user=request.user)
    dismiss = action != "restore"
    if progress.dismissed != dismiss:
        progress.dismissed = dismiss
        progress.save(update_fields=["dismissed", "updated_at"])

    return redirect(next_url)


# --- Production Simulations Management ---
@indy_hub_access_required
@login_required
def production_simulations(request):
    """
    Management page for saved production simulations.
    """
    simulations = (
        ProductionSimulation.objects.filter(user=request.user)
        .order_by("-updated_at")
        .prefetch_related("production_configs")
    )

    total_simulations, stats = summarize_simulations(simulations)

    context = {
        "simulations": simulations,
        "total_simulations": total_simulations,
        "stats": stats,
    }
    context.update(build_nav_context(request.user, active_tab="personal"))

    return render(request, "indy_hub/production_simulations.html", context)


@indy_hub_access_required
@login_required
@require_POST
def delete_production_simulation(request, simulation_id):
    """
    Delete a production simulation.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    # Remove all related configurations as well
    ProductionConfig.objects.filter(
        user=request.user,
        blueprint_type_id=simulation.blueprint_type_id,
        runs=simulation.runs,
    ).delete()

    simulation_name = simulation.display_name
    simulation.delete()

    messages.success(request, f'Simulation "{simulation_name}" deleted successfully.')
    return redirect("indy_hub:production_simulations")


@indy_hub_access_required
@login_required
def rename_production_simulation(request, simulation_id):
    """
    Rename a production simulation.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        new_name = request.POST.get("simulation_name", "").strip()
        simulation.simulation_name = new_name
        simulation.save(update_fields=["simulation_name"])

        messages.success(request, f'Simulation renamed to "{simulation.display_name}".')
        return redirect("indy_hub:production_simulations")

    context = {"simulation": simulation}
    context.update(build_nav_context(request.user, active_tab="personal"))

    return render(request, "indy_hub/rename_simulation.html", context)
