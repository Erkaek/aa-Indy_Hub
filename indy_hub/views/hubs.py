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
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import UserProfile
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
_COPY_SHARING_SCOPE_VALUES = {
    key for key, _label in CharacterSettings.COPY_SHARING_SCOPE_CHOICES
}
_NOTIFY_FREQUENCY_VALUES = {
    key for key, _label in CharacterSettings.JOB_NOTIFICATION_FREQUENCY_CHOICES
}


def _parse_bool_filter(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_user_corporation_data(
    visible_user_ids: list[int],
) -> tuple[dict[int, list[dict[str, object]]], dict[int, str]]:
    corp_rows = (
        UserProfile.objects.filter(user_id__in=visible_user_ids)
        .exclude(main_character__corporation_id__isnull=True)
        .values("user_id", "main_character__corporation_id")
        .annotate(corporation_name=Max("main_character__corporation_name"))
        .order_by("user_id")
    )

    corp_by_user_id: dict[int, list[dict[str, object]]] = {}
    corp_name_by_id: dict[int, str] = {}
    for row in corp_rows:
        corp_id = int(row["main_character__corporation_id"])
        corp_name = (row.get("corporation_name") or str(corp_id)).strip() or str(
            corp_id
        )
        corp_name_by_id[corp_id] = corp_name
        corp_by_user_id[int(row["user_id"])] = [
            {"corporation_id": corp_id, "corporation_name": corp_name}
        ]

    return corp_by_user_id, corp_name_by_id


def _parse_optional_int(value: str | None) -> int | None:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _build_settings_admin_users_state(
    user,
    params,
    *,
    include_page_usage_details: bool = True,
    include_global_usage_detail: bool = True,
) -> dict[str, object]:
    visible_users_qs = (
        get_visible_indy_hub_users_for_admin_scope(user)
        .only("id", "username")
        .order_by("username", "id")
        .distinct()
    )
    visible_user_ids = [
        int(user_id) for user_id in visible_users_qs.values_list("id", flat=True)
    ]

    corp_by_user_id, corp_name_by_id = _build_user_corporation_data(visible_user_ids)

    managed_corp_ids_for_scope = (
        sorted(get_managed_corporation_ids_for_user_admin_scope(user))
        if not user.is_superuser
        else sorted(corp_name_by_id.keys())
    )
    allowed_corp_ids = set(managed_corp_ids_for_scope)

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

    has_server_filters = bool(
        selected_corporation_id is not None
        or filter_incomplete
        or filter_inactive
        or filter_health_level
        or filter_max_health_score is not None
    )

    def _build_page_rows(
        page_users: list,
        *,
        settings_by_user_id: dict[int, CharacterSettings],
        usage_summary_by_user_id: dict[int, IndyHubUserUsage],
        scope_status_by_user_id: dict[int, dict[str, object]],
    ) -> list[dict[str, object]]:
        page_rows: list[dict[str, object]] = []
        for user_obj in page_users:
            user_id = int(user_obj.id)
            user_settings = settings_by_user_id.get(user_id)
            user_usage = usage_summary_by_user_id.get(user_id)
            corp_entries = list(corp_by_user_id.get(user_id, []))
            if not user.is_superuser:
                corp_entries = [
                    entry
                    for entry in corp_entries
                    if int(entry["corporation_id"]) in allowed_corp_ids
                ]

            sharing_scope = CharacterSettings.SCOPE_NONE
            notify_frequency = CharacterSettings.NOTIFY_DISABLED
            if user_settings:
                if user_settings.copy_sharing_scope in _COPY_SHARING_SCOPE_VALUES:
                    sharing_scope = user_settings.copy_sharing_scope
                if user_settings.jobs_notify_frequency in _NOTIFY_FREQUENCY_VALUES:
                    notify_frequency = user_settings.jobs_notify_frequency

            scope_status = scope_status_by_user_id.get(user_id)
            if scope_status is None:
                scope_status = collect_user_scope_status(user_obj)
            scope_flags = dict(scope_status.get("flags") or {})
            missing_scopes = list(scope_status.get("missing_labels") or [])
            is_inactive = bool(
                not user_usage
                or not user_usage.last_used_at
                or user_usage.last_used_at < inactive_cutoff
            )
            activity_30d_count = int(getattr(user_usage, "activity_30d_count", 0) or 0)
            health = build_user_health_score(
                scope_flags=scope_flags,
                settings_obj=user_settings,
                is_inactive=is_inactive,
                activity_30d_count=activity_30d_count,
            )
            problems = detect_admin_user_reminder_problems(
                missing_scopes=missing_scopes,
                notifications_off=(
                    notify_frequency == CharacterSettings.NOTIFY_DISABLED
                ),
                is_inactive=is_inactive,
                health_level=str(health.get("level") or "critical"),
            )

            page_rows.append(
                {
                    "user": user_obj,
                    "sharing_scope": sharing_scope,
                    "notify_frequency": notify_frequency,
                    "scope_is_complete": bool(scope_status.get("is_complete")),
                    "scope_flags": scope_flags,
                    "missing_scopes": missing_scopes,
                    "corporations": corp_entries,
                    "is_inactive": is_inactive,
                    "last_used_at": user_usage.last_used_at if user_usage else None,
                    "activity_30d_count": activity_30d_count,
                    "total_usage_count": (
                        int(user_usage.total_usage_count) if user_usage else 0
                    ),
                    "_usage_obj": user_usage,
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
                            [
                                item
                                for item in problems
                                if item.get("severity") == "comfort"
                            ]
                        ),
                    },
                }
            )

        return page_rows

    if not has_server_filters and not include_global_usage_detail:
        paginator = Paginator(visible_users_qs, _ADMIN_USERS_PAGE_SIZE)
        page_obj = paginator.get_page(_parse_optional_int(params.get("page")) or 1)
        page_users = list(page_obj.object_list)
        page_user_ids = [int(page_user.id) for page_user in page_users]

        settings_by_user_id = {
            int(obj.user_id): obj
            for obj in CharacterSettings.objects.filter(
                user_id__in=page_user_ids,
                character_id=0,
            )
        }
        usage_summary_by_user_id = {
            int(obj.user_id): obj
            for obj in IndyHubUserUsage.objects.filter(user_id__in=page_user_ids).only(
                "user_id",
                "last_used_at",
                "total_usage_count",
                "activity_30d_count",
            )
        }
        scope_status_by_user_id = collect_user_scope_status_map(page_user_ids)

        page_rows = _build_page_rows(
            page_users,
            settings_by_user_id=settings_by_user_id,
            usage_summary_by_user_id=usage_summary_by_user_id,
            scope_status_by_user_id=scope_status_by_user_id,
        )

        if include_page_usage_details:
            for row in page_rows:
                row["usage_detail"] = build_indy_hub_usage_detail(row.get("_usage_obj"))
                row.pop("_usage_obj", None)
        else:
            for row in page_rows:
                row.pop("_usage_obj", None)

        current_query = _build_admin_user_filters_query(
            {
                "selected_corporation_id": selected_corporation_id,
                "filter_incomplete": filter_incomplete,
                "filter_inactive": filter_inactive,
                "filter_health_level": filter_health_level,
                "filter_max_health_score": filter_max_health_score,
            }
        )

        return {
            "all_rows": page_rows,
            "rows": page_rows,
            "all_rows_count": int(visible_users_qs.count()),
            "global_usage_detail": None,
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

    visible_users = list(visible_users_qs)
    settings_by_user_id = {
        int(obj.user_id): obj
        for obj in CharacterSettings.objects.filter(
            user_id__in=visible_user_ids,
            character_id=0,
        )
    }
    usage_summary_by_user_id = {
        int(obj.user_id): obj
        for obj in IndyHubUserUsage.objects.filter(user_id__in=visible_user_ids).only(
            "user_id",
            "last_used_at",
            "total_usage_count",
            "activity_30d_count",
            "daily_usage",
        )
    }
    scope_status_by_user_id = collect_user_scope_status_map(visible_user_ids)

    filtered_user_ids: list[int] = []
    filtered_usage_user_ids: list[int] = []
    active_user_count_30d = 0

    for user_obj in visible_users:
        user_id = int(user_obj.id)
        user_settings = settings_by_user_id.get(user_id)
        user_usage = usage_summary_by_user_id.get(user_id)
        corp_entries = list(corp_by_user_id.get(user_id, []))
        if not user.is_superuser:
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

        scope_status = scope_status_by_user_id.get(user_id)
        if scope_status is None:
            scope_status = collect_user_scope_status(user_obj)
        scope_flags = dict(scope_status.get("flags") or {})
        scope_is_complete = bool(scope_status.get("is_complete"))
        is_inactive = bool(
            not user_usage
            or not user_usage.last_used_at
            or user_usage.last_used_at < inactive_cutoff
        )
        activity_30d_count = int(getattr(user_usage, "activity_30d_count", 0) or 0)

        if filter_incomplete and scope_is_complete:
            continue
        if filter_inactive and not is_inactive:
            continue

        if filter_health_level or filter_max_health_score is not None:
            health = build_user_health_score(
                scope_flags=scope_flags,
                settings_obj=user_settings,
                is_inactive=is_inactive,
                activity_30d_count=activity_30d_count,
            )
            if filter_health_level and health.get("level") != filter_health_level:
                continue
            if (
                filter_max_health_score is not None
                and int(health.get("score", 0)) > filter_max_health_score
            ):
                continue

        filtered_user_ids.append(user_id)
        if user_usage:
            filtered_usage_user_ids.append(user_id)
        if activity_30d_count > 0:
            active_user_count_30d += 1

    filtered_users_qs = visible_users_qs.filter(id__in=filtered_user_ids)
    paginator = Paginator(filtered_users_qs, _ADMIN_USERS_PAGE_SIZE)
    page_obj = paginator.get_page(_parse_optional_int(params.get("page")) or 1)
    page_users = list(page_obj.object_list)

    usage_by_user_id: dict[int, IndyHubUserUsage] = {}
    if include_page_usage_details and page_users:
        page_user_ids = [int(page_user.id) for page_user in page_users]
        usage_by_user_id = {
            int(obj.user_id): obj
            for obj in IndyHubUserUsage.objects.filter(user_id__in=page_user_ids)
        }

    page_rows = _build_page_rows(
        page_users,
        settings_by_user_id=settings_by_user_id,
        usage_summary_by_user_id={
            **usage_summary_by_user_id,
            **usage_by_user_id,
        },
        scope_status_by_user_id=scope_status_by_user_id,
    )

    if include_page_usage_details:
        for row in page_rows:
            row["usage_detail"] = build_indy_hub_usage_detail(row.get("_usage_obj"))
            row.pop("_usage_obj", None)
    else:
        for row in page_rows:
            row.pop("_usage_obj", None)

    current_query = _build_admin_user_filters_query(
        {
            "selected_corporation_id": selected_corporation_id,
            "filter_incomplete": filter_incomplete,
            "filter_inactive": filter_inactive,
            "filter_health_level": filter_health_level,
            "filter_max_health_score": filter_max_health_score,
        }
    )
    global_usage_detail: dict[str, object] | None = None
    if include_global_usage_detail:
        full_usage_by_user_id = {
            int(obj.user_id): obj
            for obj in IndyHubUserUsage.objects.filter(
                user_id__in=filtered_usage_user_ids
            )
        }
        rows_usage_objects = [
            full_usage_by_user_id[user_id]
            for user_id in filtered_user_ids
            if user_id in full_usage_by_user_id
        ]
        global_usage_detail = build_indy_hub_global_usage_detail(rows_usage_objects)
        global_usage_detail["visible_user_count"] = len(filtered_user_ids)
        global_usage_detail["active_user_count_30d"] = active_user_count_30d

    return {
        "all_rows": page_rows,
        "rows": page_rows,
        "all_rows_count": len(filtered_user_ids),
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


def _settings_admin_users_scope_allowed(user) -> bool:
    return bool(can_access_indy_hub_user_admin_scope(user))


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

    if not _settings_admin_users_scope_allowed(request.user):
        messages.error(
            request,
            _("You do not have permission to access Indy Hub user administration."),
        )
        return redirect("indy_hub:index")

    base_context = _build_settings_hub_context(request)
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_view_corporation_bp = can_view_corporation_blueprints(request.user)
    can_view_corporation_jobs_flag = can_view_corporation_jobs(request.user)

    state = _build_settings_admin_users_state(
        request.user,
        request.GET,
        include_page_usage_details=False,
        include_global_usage_detail=False,
    )

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
def settings_admin_users_global_usage_fragment(request):
    emit_view_analytics_event(
        view_name="settings_admin_users_global_usage_fragment", request=request
    )

    if not _settings_admin_users_scope_allowed(request.user):
        return JsonResponse({"error": "forbidden"}, status=403)

    state = _build_settings_admin_users_state(
        request.user,
        request.GET,
        include_page_usage_details=False,
        include_global_usage_detail=True,
    )
    html = render_to_string(
        "indy_hub/settings/partials/admin_users_global_usage_content.html",
        {
            "global_usage_detail": state["global_usage_detail"],
        },
        request=request,
    )
    return JsonResponse({"html": html})


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def settings_admin_users_usage_detail_fragment(request, user_id: int):
    emit_view_analytics_event(
        view_name="settings_admin_users_usage_detail_fragment", request=request
    )

    if not _settings_admin_users_scope_allowed(request.user):
        return JsonResponse({"error": "forbidden"}, status=403)

    visible_user = (
        get_visible_indy_hub_users_for_admin_scope(request.user)
        .filter(id=user_id)
        .order_by("id")
        .first()
    )
    if visible_user is None:
        return JsonResponse({"error": "not_found"}, status=404)

    usage_obj = IndyHubUserUsage.objects.filter(user_id=int(visible_user.id)).first()
    usage_detail = build_indy_hub_usage_detail(usage_obj)
    html = render_to_string(
        "indy_hub/settings/partials/admin_user_usage_modal_body.html",
        {
            "usage_detail": usage_detail,
            "usage_user": visible_user,
        },
        request=request,
    )
    return JsonResponse({"html": html})


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def test_darkly_theme(request):
    """Test page for darkly theme CSS overrides."""
    emit_view_analytics_event(view_name="test_darkly_theme", request=request)
    logger.debug("Darkly theme test page accessed (user_id=%s)", request.user.id)
    return render(request, "indy_hub/test_darkly_theme.html")
