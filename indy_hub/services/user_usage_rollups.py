"""Prepared usage analytics for the scalable admin-users dashboard."""

from __future__ import annotations

# Standard Library
from datetime import date, timedelta
from hashlib import sha1
from types import SimpleNamespace

# Django
from django.db import transaction
from django.db.models import F, Max, Min, Q, Sum
from django.utils import timezone

# AA Example App
# Local
from indy_hub.models import IndyHubUsageDailyRollup, IndyHubUserUsage
from indy_hub.services.user_usage import (
    USAGE_HISTORY_WINDOW_DAYS,
    _normalize_daily_counts,
    build_indy_hub_global_usage_detail,
    is_usage_tracking_path_excluded,
)

ROLLUP_PAGE_KEY_MAX_LENGTH = 255
ROLLUP_REBUILD_BATCH_SIZE = 25
ROLLUP_REBUILD_MAX_BATCH_SIZE = 100


def normalize_rollup_page_key(raw_page_key: object) -> str:
    """Return a stable index-safe page key without silently merging long paths."""
    page_key = str(raw_page_key or "").strip()
    if len(page_key) <= ROLLUP_PAGE_KEY_MAX_LENGTH:
        return page_key
    digest = sha1(page_key.encode("utf-8")).hexdigest()[:20]
    prefix_length = ROLLUP_PAGE_KEY_MAX_LENGTH - len(digest) - 1
    return f"{page_key[:prefix_length]}:{digest}"


def _rollup_window(reference_day: date | None = None) -> tuple[date, date]:
    end_day = reference_day or timezone.localdate()
    return end_day - timedelta(days=USAGE_HISTORY_WINDOW_DAYS - 1), end_day


def _build_rollup_rows(
    usage: IndyHubUserUsage,
    *,
    reference_day: date | None = None,
) -> list[IndyHubUsageDailyRollup]:
    start_day, end_day = _rollup_window(reference_day)
    rows: list[IndyHubUsageDailyRollup] = []

    for day_key, count in _normalize_daily_counts(usage.daily_usage).items():
        usage_day = date.fromisoformat(day_key)
        if start_day <= usage_day <= end_day:
            rows.append(
                IndyHubUsageDailyRollup(
                    usage=usage,
                    usage_day=usage_day,
                    page_key=IndyHubUsageDailyRollup.OVERALL_PAGE_KEY,
                    usage_count=int(count),
                )
            )

    raw_page_usage = usage.page_usage
    if not isinstance(raw_page_usage, dict):
        return rows

    for raw_page_key, page_data in raw_page_usage.items():
        if not isinstance(page_data, dict):
            continue
        if is_usage_tracking_path_excluded(str(raw_page_key)):
            continue
        page_key = normalize_rollup_page_key(raw_page_key)
        if not page_key:
            continue
        page_label = str(page_data.get("label") or raw_page_key)[:255]
        for day_key, count in _normalize_daily_counts(
            page_data.get("daily_usage")
        ).items():
            usage_day = date.fromisoformat(day_key)
            if start_day <= usage_day <= end_day:
                rows.append(
                    IndyHubUsageDailyRollup(
                        usage=usage,
                        usage_day=usage_day,
                        page_key=page_key,
                        page_label=page_label,
                        usage_count=int(count),
                    )
                )
    return rows


def rebuild_indy_hub_usage_rollup(usage_id: int) -> int | None:
    """Idempotently replace one user's prepared counters from retained JSON."""
    with transaction.atomic():
        try:
            usage = IndyHubUserUsage.objects.select_for_update().get(pk=int(usage_id))
        except IndyHubUserUsage.DoesNotExist:
            return None

        rows = _build_rollup_rows(usage)
        IndyHubUsageDailyRollup.objects.filter(usage=usage).delete()
        if rows:
            IndyHubUsageDailyRollup.objects.bulk_create(rows, batch_size=500)
        IndyHubUserUsage.objects.filter(pk=usage.pk).update(
            rollup_synced_at=usage.updated_at
        )
        return len(rows)


def rebuild_indy_hub_usage_rollups(usage_ids) -> tuple[int, int]:
    """Rebuild a bounded collection, returning users and rows rebuilt."""
    rebuilt_users = 0
    rebuilt_rows = 0
    for usage_id in usage_ids:
        row_count = rebuild_indy_hub_usage_rollup(int(usage_id))
        if row_count is not None:
            rebuilt_users += 1
            rebuilt_rows += row_count
    return rebuilt_users, rebuilt_rows


def increment_indy_hub_usage_rollups(
    usage: IndyHubUserUsage,
    *,
    usage_day: date,
    page_path: str | None,
    page_label: str | None,
    mark_synced: bool,
) -> None:
    """Increment prepared counters while the caller holds the usage-row lock."""
    start_day, end_day = _rollup_window()
    rollups = IndyHubUsageDailyRollup.objects.filter(usage=usage)
    rollups.filter(usage_day__lt=start_day).delete()

    retained_page_keys = [
        normalize_rollup_page_key(key)
        for key in (usage.page_usage or {})
        if not is_usage_tracking_path_excluded(str(key))
    ]
    page_rollups = rollups.exclude(page_key=IndyHubUsageDailyRollup.OVERALL_PAGE_KEY)
    if retained_page_keys:
        page_rollups.exclude(page_key__in=retained_page_keys).delete()
    else:
        page_rollups.delete()

    if start_day <= usage_day <= end_day:
        counter_specs = [
            (IndyHubUsageDailyRollup.OVERALL_PAGE_KEY, ""),
        ]
        if page_path and not is_usage_tracking_path_excluded(page_path):
            counter_specs.append(
                (
                    normalize_rollup_page_key(page_path),
                    str(page_label or page_path)[:255],
                )
            )

        for page_key, label in counter_specs:
            row, created = IndyHubUsageDailyRollup.objects.get_or_create(
                usage=usage,
                usage_day=usage_day,
                page_key=page_key,
                defaults={
                    "page_label": label,
                    "usage_count": 1,
                },
            )
            if not created:
                IndyHubUsageDailyRollup.objects.filter(pk=row.pk).update(
                    usage_count=F("usage_count") + 1,
                    page_label=label,
                )

    if mark_synced:
        IndyHubUserUsage.objects.filter(pk=usage.pk).update(
            rollup_synced_at=usage.updated_at
        )
        usage.rollup_synced_at = usage.updated_at


def stale_usage_rollup_queryset():
    """Return source rows whose JSON is newer than the prepared counters."""
    return IndyHubUserUsage.objects.filter(
        Q(rollup_synced_at__isnull=True) | Q(rollup_synced_at__lt=F("updated_at"))
    )


def build_indy_hub_global_usage_detail_from_rollups(user_queryset):
    """Build filtered global analytics from small SQL aggregates only."""
    reference_day = timezone.localdate()
    start_day, _end_day = _rollup_window(reference_day)
    user_ids = user_queryset.order_by().values("id")
    usage_scope = IndyHubUserUsage.objects.filter(user_id__in=user_ids)
    rollup_scope = IndyHubUsageDailyRollup.objects.filter(
        usage__user_id__in=user_ids,
        usage_day__gte=start_day,
        usage_day__lte=reference_day,
    )

    usage_stats = usage_scope.aggregate(
        total_usage_count=Sum("total_usage_count"),
        first_used_at=Min("first_used_at"),
        last_used_at=Max("last_used_at"),
    )
    overall_rows = list(
        rollup_scope.filter(page_key=IndyHubUsageDailyRollup.OVERALL_PAGE_KEY)
        .values("usage_day")
        .annotate(usage_count=Sum("usage_count"))
        .order_by("usage_day")
    )
    page_scope = rollup_scope.exclude(page_key=IndyHubUsageDailyRollup.OVERALL_PAGE_KEY)
    page_stats = page_scope.aggregate(usage_count=Sum("usage_count"))
    page_rows = list(
        page_scope.values("page_key")
        .annotate(
            page_label=Max("page_label"),
            usage_count=Sum("usage_count"),
        )
        .order_by("-usage_count", "page_key")[:12]
    )

    aggregated_daily_usage = {
        row["usage_day"].isoformat(): int(row["usage_count"] or 0)
        for row in overall_rows
    }
    aggregated_page_usage: dict[str, dict[str, object]] = {}
    for row in page_rows:
        page_key = str(row["page_key"])
        count = int(row["usage_count"] or 0)
        aggregated_page_usage[page_key] = {
            "label": str(row["page_label"] or page_key),
            "path": page_key,
            "total_usage_count": count,
            "daily_usage": {reference_day.isoformat(): count},
        }
    top_page_count = sum(
        int(page_data["total_usage_count"])
        for page_data in aggregated_page_usage.values()
    )
    remaining_page_count = int(page_stats["usage_count"] or 0) - top_page_count
    if remaining_page_count > 0:
        aggregated_page_usage["(grouped)"] = {
            "label": "Other pages",
            "path": "(grouped)",
            "total_usage_count": remaining_page_count,
            "daily_usage": {reference_day.isoformat(): remaining_page_count},
            "is_grouped": True,
        }

    total_usage_count = int(usage_stats["total_usage_count"] or 0)
    synthetic_usage = SimpleNamespace(
        first_used_at=usage_stats["first_used_at"],
        last_used_at=usage_stats["last_used_at"],
        total_usage_count=total_usage_count,
        daily_usage=aggregated_daily_usage,
        page_usage=aggregated_page_usage,
    )
    detail = build_indy_hub_global_usage_detail([synthetic_usage])
    detail.update(
        {
            "visible_user_count": user_queryset.order_by().count(),
            "active_user_count_30d": rollup_scope.filter(
                page_key=IndyHubUsageDailyRollup.OVERALL_PAGE_KEY,
                usage_count__gt=0,
            )
            .values("usage_id")
            .distinct()
            .count(),
            "total_usage_count": total_usage_count,
            "rollup_pending_user_count": stale_usage_rollup_queryset()
            .filter(user_id__in=user_ids)
            .count(),
        }
    )
    return detail
