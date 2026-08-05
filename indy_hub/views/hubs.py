"""Hub/landing pages for top navigation tabs."""

from __future__ import annotations

# Standard Library
from datetime import timedelta
from urllib.parse import urlencode

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger

# Local
from ..decorators import indy_hub_permission_required
from ..models import CharacterSettings, IndyHubUserUsage
from ..services.admin_user_bulk_actions import (
    collect_user_scope_status,
    collect_user_scope_status_map,
    detect_admin_user_reminder_problems,
)
from ..services.admin_user_visibility import (
    can_access_indy_hub_user_admin_scope,
    get_managed_corporation_ids_for_user_admin_scope,
    get_visible_indy_hub_users_for_admin_scope,
)
from ..services.corporation_blueprint_visibility import (
    can_view_corporation_blueprints,
    can_view_corporation_jobs,
)
from ..services.user_health import build_user_health_score
from ..services.user_usage import (
    build_indy_hub_global_usage_detail,
    build_indy_hub_usage_detail,
)
from ..utils.analytics import emit_view_analytics_event
from .navigation import build_nav_context
from .user import _build_settings_hub_context

logger = get_extension_logger(__name__)

_INACTIVITY_WINDOW_DAYS = 30
_ADMIN_USERS_PAGE_SIZE = 25


def _parse_bool_filter(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_user_corporation_data(
    visible_user_ids: list[int],
) -> tuple[dict[int, list[dict[str, object]]], dict[int, str]]:
    corp_rows = (
        CharacterOwnership.objects.filter(user_id__in=visible_user_ids)
        .exclude(character__corporation_id__isnull=True)
        .values("user_id", "character__corporation_id")
        .annotate(corporation_name=Max("character__corporation_name"))
        .order_by("user_id", "character__corporation_id")
    )

    corp_by_user_id: dict[int, list[dict[str, object]]] = {}
    corp_name_by_id: dict[int, str] = {}
    for row in corp_rows:
        corp_id = int(row["character__corporation_id"])
        corp_name = (row.get("corporation_name") or str(corp_id)).strip() or str(
            corp_id
        )
        corp_name_by_id[corp_id] = corp_name
        corp_by_user_id.setdefault(int(row["user_id"]), []).append(
            {"corporation_id": corp_id, "corporation_name": corp_name}
        )

    return corp_by_user_id, corp_name_by_id


def _parse_optional_int(value: str | None) -> int | None:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _build_settings_admin_users_state(user, params) -> dict[str, object]:
    visible_users = list(
        get_visible_indy_hub_users_for_admin_scope(user)
        .order_by("username", "id")
        .distinct()
    )
    visible_user_ids = [int(row.id) for row in visible_users]

    settings_by_user_id = {
        int(obj.user_id): obj
        for obj in CharacterSettings.objects.filter(
            user_id__in=visible_user_ids,
            character_id=0,
        )
    }
    usage_by_user_id = {
        int(obj.user_id): obj
        for obj in IndyHubUserUsage.objects.filter(user_id__in=visible_user_ids)
    }
    corp_by_user_id, corp_name_by_id = _build_user_corporation_data(visible_user_ids)
    scope_status_by_user_id = collect_user_scope_status_map(visible_user_ids)

    managed_corp_ids_for_scope = (
        sorted(get_managed_corporation_ids_for_user_admin_scope(user))
        if not user.is_superuser
        else sorted(corp_name_by_id.keys())
    )

    managed_corp_param = (params.get("managed_corporation_id") or "").strip()
    selected_corporation_id: int | None = None
    if managed_corp_param:
        try:
            selected_candidate = int(managed_corp_param)
        except ValueError:
            selected_candidate = None
        if selected_candidate in set(managed_corp_ids_for_scope):
            selected_corporation_id = selected_candidate

    filter_incomplete = _parse_bool_filter(params.get("incomplete"))
    filter_inactive = _parse_bool_filter(params.get("inactive"))
    filter_health_level = (params.get("health_level") or "").strip().lower()
    if filter_health_level not in {"", "good", "medium", "critical"}:
        filter_health_level = ""
    filter_max_health_score = _parse_optional_int(params.get("max_health_score"))
    if filter_max_health_score is not None:
        filter_max_health_score = max(0, min(100, filter_max_health_score))
    inactive_cutoff = timezone.now() - timedelta(days=_INACTIVITY_WINDOW_DAYS)

    rows: list[dict[str, object]] = []
    rows_usage_objects: list[IndyHubUserUsage] = []
    for user_obj in visible_users:
        user_id = int(user_obj.id)
        user_settings = settings_by_user_id.get(user_id)
        user_usage = usage_by_user_id.get(user_id)
        corp_entries = list(corp_by_user_id.get(user_id, []))
        if not user.is_superuser:
            allowed_corp_ids = set(managed_corp_ids_for_scope)
            corp_entries = [
                entry
                for entry in corp_entries
                if int(entry["corporation_id"]) in allowed_corp_ids
            ]
        corp_ids = {int(entry["corporation_id"]) for entry in corp_entries}
        if (
            selected_corporation_id is not None
            and selected_corporation_id not in corp_ids
        ):
            continue

        sharing_scope = CharacterSettings.SCOPE_NONE
        notify_frequency = CharacterSettings.NOTIFY_DISABLED
        if user_settings:
            if user_settings.copy_sharing_scope in dict(
                CharacterSettings.COPY_SHARING_SCOPE_CHOICES
            ):
                sharing_scope = user_settings.copy_sharing_scope
            if user_settings.jobs_notify_frequency in dict(
                CharacterSettings.JOB_NOTIFICATION_FREQUENCY_CHOICES
            ):
                notify_frequency = user_settings.jobs_notify_frequency

        scope_status = scope_status_by_user_id.get(
            user_id, collect_user_scope_status(user_obj)
        )
        scope_flags = dict(scope_status.get("flags") or {})
        scope_is_complete = bool(scope_status.get("is_complete"))
        missing_scopes = list(scope_status.get("missing_labels") or [])
        is_inactive = bool(
            not user_usage
            or not user_usage.last_used_at
            or user_usage.last_used_at < inactive_cutoff
        )

        if filter_incomplete and scope_is_complete:
            continue
        if filter_inactive and not is_inactive:
            continue

        health = build_user_health_score(
            scope_flags=scope_flags,
            settings_obj=user_settings,
            is_inactive=is_inactive,
            activity_30d_count=(
                int(user_usage.activity_30d_count) if user_usage else 0
            ),
        )
        usage_detail = build_indy_hub_usage_detail(user_usage)
        problems = detect_admin_user_reminder_problems(
            missing_scopes=missing_scopes,
            notifications_off=False,
            is_inactive=is_inactive,
            health_level=str(health.get("level") or "critical"),
        )

        if filter_health_level and health.get("level") != filter_health_level:
            continue
        if (
            filter_max_health_score is not None
            and int(health.get("score", 0)) > filter_max_health_score
        ):
            continue

        rows.append(
            {
                "user": user_obj,
                "sharing_scope": sharing_scope,
                "notify_frequency": notify_frequency,
                "scope_is_complete": scope_is_complete,
                "scope_flags": scope_flags,
                "missing_scopes": missing_scopes,
                "corporations": corp_entries,
                "is_inactive": is_inactive,
                "last_used_at": user_usage.last_used_at if user_usage else None,
                "activity_30d_count": (
                    int(user_usage.activity_30d_count) if user_usage else 0
                ),
                "total_usage_count": (
                    int(user_usage.total_usage_count) if user_usage else 0
                ),
                "usage_detail": usage_detail,
                "health": health,
                "problem_counts": {
                    "blocking": len(
                        [
                            item
                            for item in problems
                            if item.get("severity") == "blocking"
                        ]
                    ),
                    "comfort": len(
                        [item for item in problems if item.get("severity") == "comfort"]
                    ),
                },
            }
        )
        if user_usage:
            rows_usage_objects.append(user_usage)

    paginator = Paginator(rows, _ADMIN_USERS_PAGE_SIZE)
    page_obj = paginator.get_page(_parse_optional_int(params.get("page")) or 1)
    current_query = _build_admin_user_filters_query(
        {
            "selected_corporation_id": selected_corporation_id,
            "filter_incomplete": filter_incomplete,
            "filter_inactive": filter_inactive,
            "filter_health_level": filter_health_level,
            "filter_max_health_score": filter_max_health_score,
        }
    )
    global_usage_detail = build_indy_hub_global_usage_detail(rows_usage_objects)

    return {
        "all_rows": rows,
        "rows": list(page_obj.object_list),
        "all_rows_count": len(rows),
        "global_usage_detail": global_usage_detail,
        "page_obj": page_obj,
        "is_paginated": page_obj.paginator.num_pages > 1,
        "current_query": current_query,
        "filter_incomplete": filter_incomplete,
        "filter_inactive": filter_inactive,
        "filter_health_level": filter_health_level,
        "filter_max_health_score": filter_max_health_score,
        "has_max_health_score": filter_max_health_score is not None,
        "managed_corporation_choices": [
            {
                "corporation_id": corp_id,
                "corporation_name": corp_name_by_id.get(corp_id, str(corp_id)),
            }
            for corp_id in managed_corp_ids_for_scope
        ],
        "selected_corporation_id": selected_corporation_id,
    }


def _build_admin_user_filters_query(state: dict[str, object]) -> str:
    query: dict[str, str] = {}
    selected_corp_id = state.get("selected_corporation_id")
    if selected_corp_id is not None:
        query["managed_corporation_id"] = str(selected_corp_id)
    if state.get("filter_incomplete"):
        query["incomplete"] = "1"
    if state.get("filter_inactive"):
        query["inactive"] = "1"
    if state.get("filter_health_level"):
        query["health_level"] = str(state["filter_health_level"])
    if state.get("filter_max_health_score") is not None:
        query["max_health_score"] = str(state["filter_max_health_score"])
    return urlencode(query)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def settings_hub(request):
    emit_view_analytics_event(view_name="settings_hub", request=request)
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_view_corporation_bp = can_view_corporation_blueprints(request.user)
    can_view_corporation_jobs_flag = can_view_corporation_jobs(request.user)
    can_manage_material_hub = request.user.has_perm("indy_hub.can_manage_material_hub")

    logger.debug(
        "Settings hub accessed (user_id=%s, can_manage_corp=%s, can_manage_material_hub=%s)",
        request.user.id,
        can_manage_corp,
        can_manage_material_hub,
    )

    context = _build_settings_hub_context(request)
    context.update(
        {
            "can_manage_material_hub": can_manage_material_hub,
            "can_manage_corp": can_manage_corp,
            "can_view_corporation_blueprints": can_view_corporation_bp,
            "can_view_corporation_jobs": can_view_corporation_jobs_flag,
            "can_access_user_admin_scope": can_access_indy_hub_user_admin_scope(
                request.user
            ),
        }
    )

    logger.debug(
        "Material exchange configs (total=%s, active=%s)",
        context["material_exchange_config_total"],
        context["material_exchange_config_active"],
    )

    context.update(
        build_nav_context(
            request.user,
            active_tab="settings",
            can_manage_corp=can_manage_corp,
            can_view_corporation_bp=can_view_corporation_bp,
            can_view_corporation_jobs_flag=can_view_corporation_jobs_flag,
            material_hub_enabled=context["material_exchange_enabled"],
        )
    )
    context["page_title"] = _("Settings")

    return render(request, "indy_hub/settings/hub.html", context)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def settings_admin_users(request):
    emit_view_analytics_event(view_name="settings_admin_users", request=request)

    if not can_access_indy_hub_user_admin_scope(request.user):
        messages.error(
            request,
            _("You do not have permission to access Indy Hub user administration."),
        )
        return redirect("indy_hub:index")

    base_context = _build_settings_hub_context(request)
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_view_corporation_bp = can_view_corporation_blueprints(request.user)
    can_view_corporation_jobs_flag = can_view_corporation_jobs(request.user)

    state = _build_settings_admin_users_state(request.user, request.GET)

    context = {
        **base_context,
        **build_nav_context(
            request.user,
            active_tab="settings",
            can_manage_corp=can_manage_corp,
            can_view_corporation_bp=can_view_corporation_bp,
            can_view_corporation_jobs_flag=can_view_corporation_jobs_flag,
            material_hub_enabled=base_context["material_exchange_enabled"],
        ),
        "page_title": _("Settings - User Admin"),
        "can_access_user_admin_scope": True,
        **state,
        "inactive_window_days": _INACTIVITY_WINDOW_DAYS,
        "notify_disabled": CharacterSettings.NOTIFY_DISABLED,
    }

    return render(request, "indy_hub/settings/admin_users.html", context)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def test_darkly_theme(request):
    """Test page for darkly theme CSS overrides."""
    emit_view_analytics_event(view_name="test_darkly_theme", request=request)
    logger.debug("Darkly theme test page accessed (user_id=%s)", request.user.id)
    return render(request, "indy_hub/test_darkly_theme.html")
