"""Hub/landing pages for top navigation tabs."""

from __future__ import annotations

# Standard Library
from datetime import timedelta
from urllib.parse import urlencode

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Lower
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Local
from ..decorators import indy_hub_permission_required
from ..models import CharacterSettings, IndyHubUserUsage, MaterialExchangeSettings
from ..services.admin_user_bulk_actions import detect_admin_user_reminder_problems
from ..services.admin_user_visibility import (
    can_access_indy_hub_user_admin_scope,
    get_visible_indy_hub_users_for_admin_scope,
)
from ..services.corporation_blueprint_visibility import (
    can_view_corporation_blueprints,
    can_view_corporation_jobs,
)
from ..services.user_usage import (
    build_indy_hub_usage_detail,
)
from ..services.user_usage_rollups import (
    build_indy_hub_global_usage_detail_from_rollups,
)
from ..utils.analytics import emit_view_analytics_event
from .navigation import build_nav_context
from .user import _build_settings_hub_context

logger = get_extension_logger(__name__)

_INACTIVITY_WINDOW_DAYS = 30
_ADMIN_USERS_PAGE_SIZE = 25
_SIGNED_BIGINT_MAX = 9_223_372_036_854_775_807
_ADMIN_USERS_SORT_FIELDS = {
    "username",
    "corporation",
    "health",
    "scopes",
    "activity",
    "usage",
}


def _parse_bool_filter(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: str | None) -> int | None:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > _SIGNED_BIGINT_MAX:
        return None
    return parsed


def _build_admin_users_queryset(user, params):
    inactive_cutoff = timezone.now() - timedelta(days=_INACTIVITY_WINDOW_DAYS)
    queryset = (
        get_visible_indy_hub_users_for_admin_scope(user)
        .select_related("indy_hub_admin_status")
        .distinct()
    )

    activity_score = Case(
        When(
            indy_hub_admin_status__last_used_at__gte=inactive_cutoff,
            indy_hub_admin_status__activity_30d_count__gte=5,
            then=Value(30),
        ),
        When(
            indy_hub_admin_status__last_used_at__gte=inactive_cutoff,
            then=Value(15),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    health_score = ExpressionWrapper(
        Coalesce(
            "indy_hub_admin_status__scope_score",
            Value(0),
            output_field=IntegerField(),
        )
        + Coalesce(
            "indy_hub_admin_status__settings_score",
            Value(0),
            output_field=IntegerField(),
        )
        + activity_score,
        output_field=IntegerField(),
    )
    queryset = queryset.annotate(
        admin_status_exists=Case(
            When(indy_hub_admin_status__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        admin_main_character_name=Coalesce(
            "indy_hub_admin_status__main_character_name",
            Value(""),
            output_field=CharField(),
        ),
        admin_corporation_name=Coalesce(
            "indy_hub_admin_status__corporation_name",
            Value(""),
            output_field=CharField(),
        ),
        admin_scope_blueprints=Coalesce(
            "indy_hub_admin_status__scope_blueprints",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_scope_jobs=Coalesce(
            "indy_hub_admin_status__scope_jobs",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_scope_assets=Coalesce(
            "indy_hub_admin_status__scope_assets",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_scope_skills=Coalesce(
            "indy_hub_admin_status__scope_skills",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_scope_online=Coalesce(
            "indy_hub_admin_status__scope_online",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_scope_complete=Coalesce(
            "indy_hub_admin_status__scope_complete",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_notifications_enabled=Coalesce(
            "indy_hub_admin_status__notifications_enabled",
            Value(False),
            output_field=BooleanField(),
        ),
        admin_activity_30d_count=Coalesce(
            "indy_hub_admin_status__activity_30d_count",
            Value(0),
            output_field=IntegerField(),
        ),
        admin_total_usage_count=Coalesce(
            "indy_hub_admin_status__total_usage_count",
            Value(0),
            output_field=IntegerField(),
        ),
        admin_is_inactive=Case(
            When(
                indy_hub_admin_status__last_used_at__gte=inactive_cutoff,
                then=Value(False),
            ),
            default=Value(True),
            output_field=BooleanField(),
        ),
        admin_health_score=health_score,
    ).annotate(
        admin_health_level=Case(
            When(admin_health_score__gte=80, then=Value("good")),
            When(admin_health_score__gte=50, then=Value("medium")),
            default=Value("critical"),
            output_field=CharField(),
        )
    )

    search_query = str(params.get("q") or "").strip()[:100]
    if search_query:
        search_filter = Q(username__icontains=search_query) | Q(
            indy_hub_admin_status__main_character_name__icontains=search_query
        )
        numeric_search = _parse_optional_int(search_query)
        if numeric_search is not None:
            search_filter |= Q(id=numeric_search) | Q(
                indy_hub_admin_status__main_character_id=numeric_search
            )
        queryset = queryset.filter(search_filter)

    corporation_query = str(params.get("corporation") or "").strip()[:100]
    selected_corporation_id = _parse_optional_int(params.get("managed_corporation_id"))
    numeric_corporation_query = _parse_optional_int(corporation_query)
    if numeric_corporation_query is not None:
        selected_corporation_id = numeric_corporation_query
    if selected_corporation_id is not None:
        queryset = queryset.filter(
            indy_hub_admin_status__corporation_id=selected_corporation_id
        )
    elif corporation_query:
        queryset = queryset.filter(
            indy_hub_admin_status__corporation_name__icontains=corporation_query
        )

    activity_filter = str(params.get("activity") or "").strip().lower()
    if not activity_filter and _parse_bool_filter(params.get("inactive")):
        activity_filter = "inactive"
    if activity_filter not in {"active", "inactive"}:
        activity_filter = ""
    if activity_filter:
        queryset = queryset.filter(admin_is_inactive=activity_filter == "inactive")

    scope_filter = str(params.get("scopes") or "").strip().lower()
    if not scope_filter and _parse_bool_filter(params.get("incomplete")):
        scope_filter = "incomplete"
    if scope_filter not in {"complete", "incomplete"}:
        scope_filter = ""
    if scope_filter:
        queryset = queryset.filter(admin_scope_complete=scope_filter == "complete")

    health_filter = str(params.get("health_level") or "").strip().lower()
    if health_filter not in {"good", "medium", "critical"}:
        health_filter = ""
    if health_filter:
        queryset = queryset.filter(admin_health_level=health_filter)

    max_health_score = _parse_optional_int(params.get("max_health_score"))
    if max_health_score is not None:
        max_health_score = max(0, min(100, max_health_score))
        queryset = queryset.filter(admin_health_score__lte=max_health_score)

    usage_filter = str(params.get("usage") or "").strip().lower()
    if usage_filter not in {"has_usage", "no_usage"}:
        usage_filter = ""
    if usage_filter == "has_usage":
        queryset = queryset.filter(admin_total_usage_count__gt=0)
    elif usage_filter == "no_usage":
        queryset = queryset.filter(admin_total_usage_count=0)

    sort_key = str(params.get("sort") or "username").strip().lower()
    if sort_key not in _ADMIN_USERS_SORT_FIELDS:
        sort_key = "username"
    sort_direction = str(params.get("direction") or "asc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"

    sort_expressions = {
        "username": Lower("username"),
        "corporation": Lower("admin_corporation_name"),
        "health": F("admin_health_score"),
        "scopes": F("admin_scope_complete"),
        "activity": F("indy_hub_admin_status__last_used_at"),
        "usage": F("admin_total_usage_count"),
    }
    sort_expression = sort_expressions[sort_key]
    if sort_direction == "desc":
        sort_expression = sort_expression.desc(nulls_last=True)
    else:
        sort_expression = sort_expression.asc(nulls_last=True)
    queryset = queryset.order_by(sort_expression, "id")

    filters = {
        "search_query": search_query,
        "corporation_query": corporation_query,
        "selected_corporation_id": selected_corporation_id,
        "activity_filter": activity_filter,
        "scope_filter": scope_filter,
        "health_filter": health_filter,
        "max_health_score": max_health_score,
        "usage_filter": usage_filter,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
    }
    return queryset, filters


def _build_admin_user_filters_query(
    filters: dict[str, object],
    *,
    sort_key: str | None = None,
    sort_direction: str | None = None,
) -> str:
    query: dict[str, str] = {}
    if filters.get("search_query"):
        query["q"] = str(filters["search_query"])
    if filters.get("corporation_query"):
        query["corporation"] = str(filters["corporation_query"])
    elif filters.get("selected_corporation_id") is not None:
        query["managed_corporation_id"] = str(filters["selected_corporation_id"])
    if filters.get("activity_filter"):
        query["activity"] = str(filters["activity_filter"])
    if filters.get("scope_filter"):
        query["scopes"] = str(filters["scope_filter"])
    if filters.get("health_filter"):
        query["health_level"] = str(filters["health_filter"])
    if filters.get("max_health_score") is not None:
        query["max_health_score"] = str(filters["max_health_score"])
    if filters.get("usage_filter"):
        query["usage"] = str(filters["usage_filter"])
    query["sort"] = str(sort_key or filters.get("sort_key") or "username")
    query["direction"] = str(sort_direction or filters.get("sort_direction") or "asc")
    return urlencode(query)


def _build_settings_admin_users_state(
    user,
    params,
    *,
    include_page_usage_details: bool = False,
    include_global_usage_detail: bool = False,
) -> dict[str, object]:
    filtered_users_qs, filters = _build_admin_users_queryset(user, params)

    paginator = Paginator(filtered_users_qs, _ADMIN_USERS_PAGE_SIZE)
    page_obj = paginator.get_page(_parse_optional_int(params.get("page")) or 1)
    page_users = list(page_obj.object_list)
    page_rows: list[dict[str, object]] = []
    for user_obj in page_users:
        scope_flags = {
            "blueprints": bool(user_obj.admin_scope_blueprints),
            "jobs": bool(user_obj.admin_scope_jobs),
            "assets": bool(user_obj.admin_scope_assets),
            "skills": bool(user_obj.admin_scope_skills),
            "online": bool(user_obj.admin_scope_online),
        }
        missing_scopes = [
            label for label, has_scope in scope_flags.items() if not has_scope
        ]
        health = {
            "score": int(user_obj.admin_health_score or 0),
            "level": str(user_obj.admin_health_level or "critical"),
        }
        problems = detect_admin_user_reminder_problems(
            missing_scopes=missing_scopes,
            notifications_off=not bool(user_obj.admin_notifications_enabled),
            is_inactive=bool(user_obj.admin_is_inactive),
            health_level=health["level"],
        )
        corporation_id = getattr(
            getattr(user_obj, "indy_hub_admin_status", None),
            "corporation_id",
            None,
        )
        corporations = []
        if corporation_id:
            corporations.append(
                {
                    "corporation_id": int(corporation_id),
                    "corporation_name": user_obj.admin_corporation_name
                    or str(corporation_id),
                }
            )
        page_rows.append(
            {
                "user": user_obj,
                "main_character_name": user_obj.admin_main_character_name,
                "scope_is_complete": bool(user_obj.admin_scope_complete),
                "scope_token_validity_confirmed": False,
                "scope_flags": scope_flags,
                "missing_scopes": missing_scopes,
                "corporations": corporations,
                "is_inactive": bool(user_obj.admin_is_inactive),
                "last_used_at": getattr(
                    getattr(user_obj, "indy_hub_admin_status", None),
                    "last_used_at",
                    None,
                ),
                "activity_30d_count": int(user_obj.admin_activity_30d_count or 0),
                "total_usage_count": int(user_obj.admin_total_usage_count or 0),
                "health": health,
                "status_is_pending": not bool(user_obj.admin_status_exists),
                "problem_counts": {
                    "blocking": sum(
                        item.get("severity") == "blocking" for item in problems
                    ),
                    "comfort": sum(
                        item.get("severity") == "comfort" for item in problems
                    ),
                },
            }
        )

    if include_page_usage_details and page_rows:
        usage_by_user_id = {
            int(obj.user_id): obj
            for obj in IndyHubUserUsage.objects.filter(
                user_id__in=[int(row["user"].id) for row in page_rows]
            )
        }
        for row in page_rows:
            row["usage_detail"] = build_indy_hub_usage_detail(
                usage_by_user_id.get(int(row["user"].id))
            )

    current_query = _build_admin_user_filters_query(filters)
    global_usage_detail: dict[str, object] | None = None
    if include_global_usage_detail:
        global_usage_detail = build_indy_hub_global_usage_detail_from_rollups(
            filtered_users_qs
        )

    sort_queries: dict[str, str] = {}
    for sort_key in _ADMIN_USERS_SORT_FIELDS:
        next_direction = "asc"
        if filters["sort_key"] == sort_key:
            next_direction = "desc" if filters["sort_direction"] == "asc" else "asc"
        sort_queries[sort_key] = _build_admin_user_filters_query(
            filters,
            sort_key=sort_key,
            sort_direction=next_direction,
        )
    page_links: list[dict[str, object]] = []
    for page_number in paginator.get_elided_page_range(page_obj.number):
        if page_number == Paginator.ELLIPSIS:
            page_links.append({"is_ellipsis": True})
            continue
        page_links.append(
            {
                "number": int(page_number),
                "is_current": int(page_number) == int(page_obj.number),
                "is_ellipsis": False,
            }
        )

    return {
        "all_rows": page_rows,
        "rows": page_rows,
        "all_rows_count": int(paginator.count),
        "global_usage_detail": global_usage_detail,
        "page_obj": page_obj,
        "is_paginated": page_obj.paginator.num_pages > 1,
        "current_query": current_query,
        "result_start": page_obj.start_index() if paginator.count else 0,
        "result_end": page_obj.end_index() if paginator.count else 0,
        "page_links": page_links,
        "sort_queries": sort_queries,
        "filters": filters,
        "selected_corporation_id": filters["selected_corporation_id"],
        "corporation_filter_value": filters["corporation_query"]
        or filters["selected_corporation_id"]
        or "",
    }


def _settings_admin_users_scope_allowed(user) -> bool:
    return bool(can_access_indy_hub_user_admin_scope(user))


def _build_settings_admin_users_context(user) -> dict[str, object]:
    """Build only the Settings/base-template context used by user admin."""

    can_manage_corp = user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_view_corporation_bp = can_view_corporation_blueprints(user)
    can_view_corporation_jobs_flag = can_view_corporation_jobs(user)
    material_hub_enabled = MaterialExchangeSettings.objects.values_list(
        "is_enabled", flat=True
    ).first()
    if material_hub_enabled is None:
        material_hub_enabled = True

    return {
        **build_nav_context(
            user,
            active_tab="settings",
            can_manage_corp=can_manage_corp,
            can_view_corporation_bp=can_view_corporation_bp,
            can_view_corporation_jobs_flag=can_view_corporation_jobs_flag,
            material_hub_enabled=bool(material_hub_enabled),
            validate_esi_tokens=False,
        ),
        "can_access_user_admin_scope": True,
    }


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

    state = _build_settings_admin_users_state(
        request.user,
        request.GET,
        include_page_usage_details=False,
        include_global_usage_detail=False,
    )

    context = {
        **_build_settings_admin_users_context(request.user),
        "page_title": _("Settings - User Admin"),
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

    filtered_users_qs, _filters = _build_admin_users_queryset(request.user, request.GET)
    global_usage_detail = build_indy_hub_global_usage_detail_from_rollups(
        filtered_users_qs
    )
    html = render_to_string(
        "indy_hub/settings/partials/admin_users_global_usage_content.html",
        {
            "global_usage_detail": global_usage_detail,
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
