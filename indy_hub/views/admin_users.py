"""Local diagnostics and explicit maintenance tools for superusers."""

from __future__ import annotations

# Standard Library
import csv
import json
from io import StringIO

# Third Party
from celery.result import AsyncResult

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Avg, BooleanField, Case, Count, Max, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

# AA Example App
from indy_hub.decorators import indy_hub_permission_required
from indy_hub.models import (
    Blueprint,
    IndustryJob,
    IndustrySkillSnapshot,
    IndyHubUserUsage,
)
from indy_hub.services.admin_user_visibility import (
    can_access_indy_hub_user_admin_scope,
    get_visible_indy_hub_users_for_admin_scope,
)
from indy_hub.services.user_usage import build_indy_hub_usage_detail
from indy_hub.tasks.housekeeping import rebuild_selected_admin_user_statuses
from indy_hub.tasks.industry import (
    _is_user_active,
    manual_refresh_allowed,
    manual_refresh_in_progress,
    request_manual_refresh,
    update_user_skill_snapshots,
)

from .hubs import _build_admin_users_queryset, _build_settings_admin_users_state

logger = get_extension_logger(__name__)
User = get_user_model()
MAX_SELECTION = 25
MAX_EXPORT = 10000


def _json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_GET
def settings_admin_users_summary(request):
    if not can_access_indy_hub_user_admin_scope(request.user):
        return _json({"error": _("Access denied.")}, 403)
    users, _filters = _build_admin_users_queryset(request.user, request.GET)
    summary = users.order_by().aggregate(
        total=Count("id", distinct=True),
        active=Count("id", filter=Q(admin_is_inactive=False), distinct=True),
        incomplete=Count(
            "id",
            filter=Q(admin_scope_complete=False, admin_status_exists=True),
            distinct=True,
        ),
        critical=Count(
            "id",
            filter=Q(admin_health_level="critical", admin_status_exists=True),
            distinct=True,
        ),
        pending=Count("id", filter=Q(admin_status_exists=False), distinct=True),
        notifications_off=Count(
            "id",
            filter=Q(admin_notifications_enabled=False, admin_status_exists=True),
            distinct=True,
        ),
        average_health=Avg("admin_health_score", filter=Q(admin_status_exists=True)),
        updated_at=Max("indy_hub_admin_status__updated_at"),
    )
    summary["average_health"] = round(summary["average_health"] or 0)
    return _json(
        {
            "html": render_to_string(
                "indy_hub/settings/partials/admin_users_summary.html",
                {"summary": summary},
                request=request,
            ),
            "summary": summary,
        }
    )


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_GET
def settings_admin_users_detail(request, user_id):
    if not can_access_indy_hub_user_admin_scope(request.user):
        return _json({"error": _("Access denied.")}, 403)
    visible = (
        get_visible_indy_hub_users_for_admin_scope(request.user)
        .filter(pk=user_id)
        .exists()
    )
    if not visible:
        return _json({"error": _("User not found.")}, 404)
    # Reuse the same scoring and permissions as the list, for this exact user.
    state = _build_settings_admin_users_state(request.user, {}, target_user_id=user_id)
    row = next((row for row in state["rows"] if row["user"].pk == user_id), None)
    if row is None:
        return _json({"error": _("User not found.")}, 404)
    target = row["user"]
    ownerships = list(
        CharacterOwnership.objects.filter(user_id=user_id)
        .select_related("character")
        .order_by("character__character_name")[:100]
    )
    # Only select public metadata; raw token/refresh secrets never enter diagnostics.
    tokens = list(
        Token.objects.filter(user_id=user_id)
        .annotate(
            renewable=Case(
                When(
                    Q(refresh_token__isnull=False) & ~Q(refresh_token=""),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .only("id", "user_id", "character_id", "character_name", "created")
        .prefetch_related("scopes")
        .order_by("character_name", "-created")[:250]
    )
    token_rows = [
        {
            "character_name": token.character_name,
            "character_id": token.character_id,
            "renewable": token.renewable,
            "expired": token.expired,
            "expires": token.expires,
            "scopes": [scope.name for scope in token.scopes.all()],
        }
        for token in tokens
    ]
    snapshots = []
    for label, model in (
        (_("Blueprints"), Blueprint),
        (_("Industry jobs"), IndustryJob),
    ):
        counts = model.objects.filter(owner_user_id=user_id).aggregate(
            personal=Count("pk", filter=Q(owner_kind=Blueprint.OwnerKind.CHARACTER)),
            corporation=Count(
                "pk", filter=Q(owner_kind=Blueprint.OwnerKind.CORPORATION)
            ),
            updated_at=Max("last_updated"),
        )
        snapshots.append({"label": label, **counts})
    skills = IndustrySkillSnapshot.objects.filter(owner_user_id=user_id).aggregate(
        count=Count("pk"), updated_at=Max("last_updated")
    )
    refreshes = []
    for kind in ("blueprints", "jobs"):
        for scope in ("character", "corporation"):
            allowed, remaining = manual_refresh_allowed(kind, user_id, scope)
            running = manual_refresh_in_progress(kind, user_id, scope)
            permitted = scope == "character" or target.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            )
            refreshes.append(
                {
                    "kind": kind,
                    "scope": scope,
                    "running": running,
                    "allowed": permitted and allowed and not running,
                    "remaining_seconds": (
                        int(remaining.total_seconds()) if remaining else 0
                    ),
                    "permitted": permitted,
                }
            )
    usage = IndyHubUserUsage.objects.filter(user_id=user_id).first()
    status = getattr(target, "indy_hub_admin_status", None)
    settings_score = status.settings_score if status else 0
    context = {
        "row": row,
        "ownerships": ownerships,
        "tokens": token_rows,
        "snapshots": snapshots,
        "skills": skills,
        "refreshes": refreshes,
        "usage_detail": build_indy_hub_usage_detail(usage),
        "usage_user": target,
        "character_limit": len(ownerships) == 100,
        "token_limit": len(tokens) == 250,
        "automatic_sync_active": _is_user_active(target),
        "scope_score": target.admin_current_scope_score,
        "settings_score": settings_score,
        "activity_score": row["health"]["score"]
        - target.admin_current_scope_score
        - settings_score,
    }
    return _json(
        {
            "html": render_to_string(
                "indy_hub/settings/partials/admin_users_detail.html",
                context,
                request=request,
            ),
            "user_id": user_id,
            "username": target.username,
        }
    )


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def settings_admin_users_actions(request):
    if not can_access_indy_hub_user_admin_scope(request.user):
        return _json({"error": _("Access denied.")}, 403)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        action = payload.get("action")
        ids = payload.get("user_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SELECTION:
            raise ValueError
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= 9223372036854775807
            for value in ids
        ):
            raise ValueError
        ids = sorted(set(ids))
        scope = payload.get("scope", "character")
        if action not in {"rebuild_status", "blueprints", "jobs"} or scope not in {
            "character",
            "corporation",
        }:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        return _json(
            {"error": _("Choose a valid action and between 1 and 25 users.")}, 400
        )
    targets = list(
        get_visible_indy_hub_users_for_admin_scope(request.user)
        .filter(pk__in=ids)
        .order_by("pk")
    )
    if len(targets) != len(ids):
        return _json(
            {
                "error": _(
                    "The selection contains an unavailable user. Refresh the list."
                )
            },
            404,
        )
    if action == "rebuild_status":
        try:
            task = rebuild_selected_admin_user_statuses.apply_async(
                kwargs={"actor_id": request.user.id, "user_ids": ids}, priority=8
            )
        except Exception:
            logger.exception(
                "Admin status rebuild dispatch failed (actor=%s)", request.user.id
            )
            return _json(
                {"error": _("Unable to schedule maintenance. Please try again.")}, 503
            )
        logger.info(
            "Admin user action actor=%s action=%s targets=%s",
            request.user.id,
            action,
            ids,
        )
        if task:
            cache.set(
                f"indy_hub:admin_maintenance:{request.user.id}:{task.id}",
                True,
                timeout=900,
            )
        return _json(
            {
                "scheduled": len(ids) if task else 0,
                "message": (
                    _("Account diagnostics rebuild scheduled.")
                    if task
                    else _("This diagnostics rebuild is already queued.")
                ),
                "task_id": str(task.id) if task else None,
            }
        )
    results = []
    for target in targets:
        if scope == "corporation" and not target.has_perm(
            "indy_hub.can_manage_corp_bp_requests"
        ):
            results.append(
                {
                    "user_id": target.id,
                    "username": target.username,
                    "scheduled": False,
                    "reason": "permission",
                    "message": _("This user cannot manage corporation industry data."),
                }
            )
            continue
        skill_warning = False
        try:
            scheduled, remaining, reason = request_manual_refresh(
                action, target.id, priority=5, scope=scope
            )
            if scheduled and action == "jobs":
                try:
                    update_user_skill_snapshots.apply_async(
                        args=(target.id,), kwargs={"force_refresh": True}, priority=5
                    )
                except Exception:
                    skill_warning = True
                    logger.exception(
                        "Admin skill refresh dispatch failed (actor=%s target=%s)",
                        request.user.id,
                        target.id,
                    )
            message = (
                _("Refresh scheduled.")
                if scheduled
                else (
                    _("Refresh already running.")
                    if reason == "in_progress"
                    else _("A recent request is still within its cooldown.")
                )
            )
            if skill_warning:
                message += " " + _(
                    "Skill snapshots could not be scheduled. Retry them from the user's industry page."
                )
            results.append(
                {
                    "user_id": target.id,
                    "username": target.username,
                    "scheduled": scheduled,
                    "reason": reason,
                    "remaining_seconds": (
                        int(remaining.total_seconds()) if remaining else 0
                    ),
                    "message": message,
                }
            )
        except Exception:
            logger.exception(
                "Admin refresh dispatch failed (actor=%s target=%s kind=%s)",
                request.user.id,
                target.id,
                action,
            )
            results.append(
                {
                    "user_id": target.id,
                    "username": target.username,
                    "scheduled": False,
                    "reason": "error",
                    "message": _("Unable to schedule this refresh. Please try again."),
                }
            )
    logger.info(
        "Admin user action actor=%s action=%s scope=%s targets=%s",
        request.user.id,
        action,
        scope,
        ids,
    )
    return _json(
        {"results": results, "scheduled": sum(row["scheduled"] for row in results)}
    )


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_GET
def settings_admin_users_action_status(request):
    if not can_access_indy_hub_user_admin_scope(request.user):
        return _json({"error": _("Access denied.")}, 403)
    task_id = request.GET.get("task_id", "")
    if task_id:
        if len(task_id) > 64 or not cache.get(
            f"indy_hub:admin_maintenance:{request.user.id}:{task_id}"
        ):
            return _json({"error": _("Maintenance request not found.")}, 404)
        result = AsyncResult(task_id)
        return _json(
            {
                "running": not result.ready(),
                "failed": result.failed(),
                "state": result.state,
            }
        )
    try:
        ids = [int(value) for value in request.GET.get("user_ids", "").split(",")]
        kind = request.GET.get("kind")
        scope = request.GET.get("scope", "character")
        if (
            not 1 <= len(ids) <= MAX_SELECTION
            or any(not 0 < value <= 9223372036854775807 for value in ids)
            or kind not in {"blueprints", "jobs"}
            or scope not in {"character", "corporation"}
        ):
            raise ValueError
    except (ValueError, TypeError):
        return _json({"error": _("Invalid refresh status request.")}, 400)
    ids = sorted(set(ids))
    if get_visible_indy_hub_users_for_admin_scope(request.user).filter(
        pk__in=ids
    ).count() != len(ids):
        return _json({"error": _("User not found.")}, 404)
    return _json(
        {
            "running": any(
                manual_refresh_in_progress(kind, user_id, scope) for user_id in ids
            )
        }
    )


def _csv_cell(value):
    value = str(value if value is not None else "")
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        value = "'" + value
    return value


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_GET
def settings_admin_users_export(request):
    if not can_access_indy_hub_user_admin_scope(request.user):
        return _json({"error": _("Access denied.")}, 403)
    users, _filters = _build_admin_users_queryset(request.user, request.GET)
    if users.count() > MAX_EXPORT:
        return _json(
            {
                "error": _(
                    "Narrow the filters to at most 10,000 users before exporting."
                )
            },
            400,
        )
    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(
        [
            "User ID",
            "Username",
            "Main character",
            "Corporation",
            "Account enabled",
            "Health score",
            "Scope coverage",
            "Notifications enabled",
            "Last Indy Hub visit",
            "Visits / 30d",
            "All-time visits",
            "Diagnostics updated",
        ]
    )
    for user in users[:MAX_EXPORT].iterator(chunk_size=500):
        status = getattr(user, "indy_hub_admin_status", None)
        writer.writerow(
            [
                _csv_cell(value)
                for value in (
                    user.id,
                    user.username,
                    user.admin_main_character_name,
                    user.admin_corporation_name,
                    user.is_active,
                    user.admin_health_score if user.admin_status_exists else "pending",
                    f"{user.admin_scope_count}/4",
                    user.admin_notifications_enabled,
                    (
                        status.last_used_at.isoformat()
                        if status and status.last_used_at
                        else ""
                    ),
                    user.admin_activity_30d_count,
                    user.admin_total_usage_count,
                    status.updated_at.isoformat() if status else "",
                )
            ]
        )
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="indy-hub-users.csv"'
    response["Cache-Control"] = "no-store"
    return response
