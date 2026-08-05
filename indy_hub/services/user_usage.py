"""Helpers for recording simple Indy Hub user usage history."""

from __future__ import annotations

# Standard Library
import math
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

# Django
from django.db import transaction
from django.utils import timezone

# AA Example App
# Local
from indy_hub.models import IndyHubUserUsage

USAGE_HISTORY_WINDOW_DAYS = 30
_PAGE_RING_PALETTE = (
    "rgba(240, 84, 129, 0.78)",
    "rgba(255, 159, 67, 0.78)",
    "rgba(255, 211, 107, 0.78)",
    "rgba(123, 205, 165, 0.78)",
    "rgba(92, 175, 255, 0.78)",
    "rgba(149, 102, 255, 0.78)",
    "rgba(255, 118, 180, 0.78)",
    "rgba(84, 214, 224, 0.78)",
)

_USAGE_EXCLUDED_PATHS = {
    "/user_notifications_count/",
}


def is_usage_tracking_path_excluded(path: str | None) -> bool:
    """Return True when a path should not be counted in usage metrics."""
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return True
    # Ignore query string/fragments and normalize trailing slash variants.
    split_path = urlsplit(normalized_path).path.strip()
    if split_path and split_path != "/":
        split_path = split_path.rstrip("/")
    normalized_path = split_path or "/"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    return (
        normalized_path in _USAGE_EXCLUDED_PATHS
        or f"{normalized_path}/" in _USAGE_EXCLUDED_PATHS
    )


def _normalize_daily_counts(raw_counts) -> dict[str, int]:
    normalized_counts: dict[str, int] = {}
    if not isinstance(raw_counts, dict):
        return normalized_counts

    for day_key, count in raw_counts.items():
        try:
            day = date.fromisoformat(str(day_key))
            normalized_count = int(count)
        except (TypeError, ValueError):
            continue
        if normalized_count <= 0:
            continue
        normalized_counts[day.isoformat()] = normalized_count

    return normalized_counts


def _parse_iso_datetime(raw_value):
    if not raw_value:
        return None
    if hasattr(raw_value, "tzinfo"):
        return raw_value
    try:
        return datetime.fromisoformat(str(raw_value))
    except (TypeError, ValueError):
        return raw_value


def build_usage_timeline(
    raw_counts,
    *,
    end_day: date | None = None,
    days: int = 7,
) -> dict[str, object]:
    normalized_counts = _normalize_daily_counts(raw_counts)
    reference_day = end_day or timezone.localdate()
    start_day = reference_day - timedelta(days=max(days - 1, 0))
    timeline: list[dict[str, object]] = []

    max_count = 0
    for offset in range(max(days, 0)):
        day = start_day + timedelta(days=offset)
        count = int(normalized_counts.get(day.isoformat(), 0))
        max_count = max(max_count, count)
        timeline.append(
            {
                "date": day,
                "label": day.strftime("%a %d"),
                "count": count,
                "iso": day.isoformat(),
            }
        )

    for item in timeline:
        count = int(item["count"])
        item["percent"] = int(round((count / max_count) * 100)) if max_count else 0
        scaled = (count / max_count) if max_count else 0
        item["svg_height"] = 4 + int(round(scaled * 32)) if max_count else 4
        item["svg_y"] = 36 - int(round(scaled * 28)) if max_count else 36

    svg_points: list[str] = []
    radial_points: list[str] = []
    radial_spokes: list[dict[str, int]] = []
    timeline_length = max(len(timeline), 1)
    center_x = 90
    center_y = 90
    outer_radius = 58
    inner_radius = 18
    for index, item in enumerate(timeline):
        angle = (-math.pi / 2) + ((2 * math.pi) * (index / timeline_length))
        scaled = (int(item["count"]) / max_count) if max_count else 0
        radius = int(round(inner_radius + (outer_radius - inner_radius) * scaled)) if max_count else inner_radius
        x = int(round(center_x + math.cos(angle) * radius))
        y = int(round(center_y + math.sin(angle) * radius))
        base_x = int(round(center_x + math.cos(angle) * outer_radius))
        base_y = int(round(center_y + math.sin(angle) * outer_radius))
        label_radius = outer_radius + 18
        label_x = int(round(center_x + math.cos(angle) * label_radius))
        label_y = int(round(center_y + math.sin(angle) * label_radius))
        svg_points.append(f"{x},{y}")
        radial_points.append(f"{x},{y}")
        radial_spokes.append(
            {
                "base_x": base_x,
                "base_y": base_y,
                "x": x,
                "y": y,
            }
        )
        item["svg_x"] = x
        item["svg_y"] = y
        item["svg_bar_x"] = x - 5
        item["svg_bar_width"] = 10
        item["radial_x"] = x
        item["radial_y"] = y
        item["radial_base_x"] = base_x
        item["radial_base_y"] = base_y
        item["radial_label_x"] = label_x
        item["radial_label_y"] = label_y

    peak_item = max(timeline, key=lambda item: int(item["count"]), default=None)
    return {
        "timeline": timeline,
        "svg_points": " ".join(svg_points),
        "radial_points": " ".join(radial_points),
        "radial_center_x": center_x,
        "radial_center_y": center_y,
        "radial_radius": outer_radius,
        "radial_spokes": radial_spokes,
        "peak_day_label": peak_item["label"] if peak_item else "",
        "peak_day_count": int(peak_item["count"]) if peak_item else 0,
        "max_count": max_count,
    }


def build_indy_hub_usage_detail(usage) -> dict[str, object]:
    if not usage:
        return {
            "has_usage": False,
            "page_rows": [],
            "overall_timeline": [],
            "active_days_30d": 0,
            "active_days_total": 0,
            "page_count": 0,
        }

    overall_daily_usage = _normalize_daily_counts(getattr(usage, "daily_usage", {}))
    reference_day = timezone.localdate(usage.last_used_at or timezone.now())
    overall_timeline_data = build_usage_timeline(
        overall_daily_usage,
        end_day=reference_day,
        days=7,
    )
    overall_timeline = overall_timeline_data["timeline"]
    active_days_total = len(overall_daily_usage)
    active_days_30d = sum(
        1 for day_key in overall_daily_usage if date.fromisoformat(day_key) >= reference_day - timedelta(days=29)
    )

    raw_page_usage = getattr(usage, "page_usage", {})
    page_rows: list[dict[str, object]] = []
    if isinstance(raw_page_usage, dict):
        for page_path, page_data in raw_page_usage.items():
            if not isinstance(page_data, dict):
                continue
            if is_usage_tracking_path_excluded(page_path):
                continue
            page_daily_usage = _normalize_daily_counts(page_data.get("daily_usage"))
            page_timeline_data = build_usage_timeline(
                page_daily_usage,
                end_day=reference_day,
                days=7,
            )
            page_timeline = page_timeline_data["timeline"]
            page_rows.append(
                {
                    "path": str(page_path),
                    "label": str(page_data.get("label") or page_path),
                    "total_usage_count": int(page_data.get("total_usage_count") or 0),
                    "first_used_at": _parse_iso_datetime(page_data.get("first_used_at")),
                    "last_used_at": _parse_iso_datetime(page_data.get("last_used_at")),
                    "recent_30d_count": sum(
                        count
                        for day_key, count in page_daily_usage.items()
                        if date.fromisoformat(day_key) >= reference_day - timedelta(days=29)
                    ),
                    "recent_7d_count": sum(
                        count
                        for day_key, count in page_daily_usage.items()
                        if date.fromisoformat(day_key) >= reference_day - timedelta(days=6)
                    ),
                    "timeline": page_timeline,
                    "peak_day_label": page_timeline_data["peak_day_label"],
                    "peak_day_count": page_timeline_data["peak_day_count"],
                }
            )

    page_rows.sort(
        key=lambda row: (
            int(row["total_usage_count"]),
            str(row["last_used_at"] or ""),
            str(row["label"]),
        ),
        reverse=True,
    )

    page_total_30d = sum(int(row["recent_30d_count"]) for row in page_rows)
    page_rings: list[dict[str, object]] = []
    if page_rows and page_total_30d > 0:
        center_x = 110
        center_y = 110
        outer_radius = 72
        inner_radius = 46

        ring_rows = [
            row
            for row in page_rows
            if int(row.get("recent_30d_count") or 0) > 0
        ]
        ring_rows.sort(
            key=lambda row: int(row.get("recent_30d_count") or 0),
            reverse=True,
        )
        primary_ring_rows = [dict(row) for row in ring_rows[:12]]
        remaining_count = sum(
            int(row.get("recent_30d_count") or 0)
            for row in ring_rows[12:]
        )
        if remaining_count > 0:
            primary_ring_rows.append(
                {
                    "label": "Other pages",
                    "path": "(grouped)",
                    "recent_30d_count": remaining_count,
                    "total_usage_count": remaining_count,
                    "page_share_percent": int(
                        round((remaining_count / page_total_30d) * 100)
                    )
                    if page_total_30d
                    else 0,
                }
            )

        current_angle = -90.0
        for index, row in enumerate(primary_ring_rows):
            count = int(row["recent_30d_count"])
            if count <= 0:
                continue
            sweep = (count / page_total_30d) * 360.0
            start_angle = current_angle
            end_angle = current_angle + sweep
            mid_angle = current_angle + (sweep / 2.0)
            label_radius = outer_radius + 18
            label_x = int(round(center_x + math.cos(math.radians(mid_angle)) * label_radius))
            label_y = int(round(center_y + math.sin(math.radians(mid_angle)) * label_radius))
            start_x = int(round(center_x + math.cos(math.radians(start_angle)) * outer_radius))
            start_y = int(round(center_y + math.sin(math.radians(start_angle)) * outer_radius))
            end_x = int(round(center_x + math.cos(math.radians(end_angle)) * outer_radius))
            end_y = int(round(center_y + math.sin(math.radians(end_angle)) * outer_radius))
            large_arc = 1 if sweep > 180 else 0
            row["page_arc_path"] = (
                f"M {int(round(center_x + math.cos(math.radians(start_angle)) * inner_radius))} "
                f"{int(round(center_y + math.sin(math.radians(start_angle)) * inner_radius))} "
                f"L {start_x} {start_y} "
                f"A {outer_radius} {outer_radius} 0 {large_arc} 1 {end_x} {end_y} "
                f"L {int(round(center_x + math.cos(math.radians(end_angle)) * inner_radius))} "
                f"{int(round(center_y + math.sin(math.radians(end_angle)) * inner_radius))} "
                f"A {inner_radius} {inner_radius} 0 {large_arc} 0 {int(round(center_x + math.cos(math.radians(start_angle)) * inner_radius))} "
                f"{int(round(center_y + math.sin(math.radians(start_angle)) * inner_radius))} Z"
            )
            row["page_arc_label_x"] = label_x
            row["page_arc_label_y"] = label_y
            row["page_arc_mid_angle"] = mid_angle
            normalized_mid_angle = (mid_angle + 360.0) % 360.0
            on_left_side = 90.0 < normalized_mid_angle < 270.0
            row["page_label_anchor"] = "end" if on_left_side else "start"
            row["page_label_rotation"] = int(round(mid_angle + (180.0 if on_left_side else 0.0)))
            row["page_label_short"] = (
                f"{str(row.get('label') or '')[:25]}..."
                if len(str(row.get("label") or "")) > 28
                else str(row.get("label") or "")
            )
            row["page_label_x"] = int(
                round(center_x + math.cos(math.radians(mid_angle)) * (outer_radius + 24))
            )
            row["page_label_y"] = int(
                round(center_y + math.sin(math.radians(mid_angle)) * (outer_radius + 24))
            )
            row["page_color"] = _PAGE_RING_PALETTE[index % len(_PAGE_RING_PALETTE)]
            row["page_share_percent"] = int(round((count / page_total_30d) * 100)) if page_total_30d else 0
            page_rings.append(row)
            current_angle = end_angle

    return {
        "has_usage": True,
        "first_used_at": _parse_iso_datetime(usage.first_used_at),
        "last_used_at": _parse_iso_datetime(usage.last_used_at),
        "total_usage_count": int(usage.total_usage_count or 0),
        "activity_7d_count": int(usage.activity_7d_count or 0),
        "activity_30d_count": int(usage.activity_30d_count or 0),
        "overall_timeline": overall_timeline,
        "active_days_30d": active_days_30d,
        "active_days_total": active_days_total,
        "peak_day_label": overall_timeline_data["peak_day_label"],
        "peak_day_count": overall_timeline_data["peak_day_count"],
        "page_rows": page_rows,
        "page_rings": page_rings,
        "page_total_30d": page_total_30d,
        "page_count": len(page_rows),
        "page_ring_center_x": 110,
        "page_ring_center_y": 110,
        "page_ring_inner_radius": 46,
        "page_ring_outer_radius": 72,
    }


def build_indy_hub_global_usage_detail(usages) -> dict[str, object]:
    """Aggregate usage detail across multiple users for global admin display."""
    usage_list = [usage for usage in list(usages or []) if usage]
    if not usage_list:
        return {
            "has_usage": False,
            "visible_user_count": 0,
            "active_user_count_30d": 0,
            "timeline_30d": [],
        }

    aggregated_daily_usage: dict[str, int] = {}
    aggregated_page_usage: dict[str, dict[str, object]] = {}
    first_used_at = None
    last_used_at = None
    total_usage_count = 0

    for usage in usage_list:
        total_usage_count += int(getattr(usage, "total_usage_count", 0) or 0)

        raw_first = _parse_iso_datetime(getattr(usage, "first_used_at", None))
        raw_last = _parse_iso_datetime(getattr(usage, "last_used_at", None))
        if raw_first and (first_used_at is None or raw_first < first_used_at):
            first_used_at = raw_first
        if raw_last and (last_used_at is None or raw_last > last_used_at):
            last_used_at = raw_last

        for day_key, count in _normalize_daily_counts(getattr(usage, "daily_usage", {})).items():
            aggregated_daily_usage[day_key] = aggregated_daily_usage.get(day_key, 0) + int(count)

        raw_page_usage = getattr(usage, "page_usage", {})
        if not isinstance(raw_page_usage, dict):
            continue

        for page_path, page_data in raw_page_usage.items():
            if not isinstance(page_data, dict):
                continue
            if is_usage_tracking_path_excluded(page_path):
                continue

            page_key = str(page_path)
            page_entry = aggregated_page_usage.get(page_key) or {
                "label": str(page_data.get("label") or page_key),
                "path": page_key,
                "total_usage_count": 0,
                "first_used_at": None,
                "last_used_at": None,
                "daily_usage": {},
            }
            page_entry["total_usage_count"] = int(page_entry.get("total_usage_count") or 0) + int(
                page_data.get("total_usage_count") or 0
            )

            page_first = _parse_iso_datetime(page_data.get("first_used_at"))
            page_last = _parse_iso_datetime(page_data.get("last_used_at"))
            current_first = _parse_iso_datetime(page_entry.get("first_used_at"))
            current_last = _parse_iso_datetime(page_entry.get("last_used_at"))
            if page_first and (current_first is None or page_first < current_first):
                page_entry["first_used_at"] = page_first.isoformat() if hasattr(page_first, "isoformat") else page_first
            if page_last and (current_last is None or page_last > current_last):
                page_entry["last_used_at"] = page_last.isoformat() if hasattr(page_last, "isoformat") else page_last

            merged_daily_usage = dict(page_entry.get("daily_usage") or {})
            for day_key, count in _normalize_daily_counts(page_data.get("daily_usage")).items():
                merged_daily_usage[day_key] = merged_daily_usage.get(day_key, 0) + int(count)
            page_entry["daily_usage"] = merged_daily_usage

            aggregated_page_usage[page_key] = page_entry

    reference_day = timezone.localdate(last_used_at or timezone.now())
    start_30d = reference_day - timedelta(days=29)
    start_7d = reference_day - timedelta(days=6)

    activity_30d_count = sum(
        count
        for day_key, count in aggregated_daily_usage.items()
        if date.fromisoformat(day_key) >= start_30d
    )
    activity_7d_count = sum(
        count
        for day_key, count in aggregated_daily_usage.items()
        if date.fromisoformat(day_key) >= start_7d
    )

    synthetic_usage = SimpleNamespace(
        first_used_at=first_used_at,
        last_used_at=last_used_at,
        total_usage_count=total_usage_count,
        activity_7d_count=activity_7d_count,
        activity_30d_count=activity_30d_count,
        daily_usage=aggregated_daily_usage,
        page_usage=aggregated_page_usage,
    )
    detail = build_indy_hub_usage_detail(synthetic_usage)
    timeline_30d_data = build_usage_timeline(
        aggregated_daily_usage,
        end_day=reference_day,
        days=30,
    )

    detail.update(
        {
            "has_usage": bool(activity_30d_count or detail.get("page_total_30d")),
            "visible_user_count": len(usage_list),
            "active_user_count_30d": len(
                [
                    usage
                    for usage in usage_list
                    if int(getattr(usage, "activity_30d_count", 0) or 0) > 0
                ]
            ),
            "timeline_30d": timeline_30d_data["timeline"],
            "timeline_30d_peak_day_label": timeline_30d_data["peak_day_label"],
            "timeline_30d_peak_day_count": timeline_30d_data["peak_day_count"],
        }
    )
    return detail


def track_indy_hub_usage_for_user(
    user,
    *,
    at=None,
    page_path: str | None = None,
    page_label: str | None = None,
) -> None:
    """Record one Indy Hub usage hit for the given user."""
    if not getattr(user, "is_authenticated", False):
        return

    with transaction.atomic():
        usage, _created = IndyHubUserUsage.objects.select_for_update().get_or_create(
            user=user
        )
        usage.register_usage(at=at, page_path=page_path, page_label=page_label)
        usage.save(
            update_fields=[
                "first_used_at",
                "last_used_at",
                "total_usage_count",
                "activity_7d_count",
                "activity_30d_count",
                "daily_usage",
                "page_usage",
                "updated_at",
            ]
        )


def track_indy_hub_usage_once_per_request(request) -> None:
    """Record usage for authenticated users at most once per request."""
    if getattr(request, "_indy_hub_usage_tracked", False):
        return

    if getattr(request, "method", "GET") != "GET":
        return

    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return

    request_path = str(getattr(request, "path", "") or "").strip()
    if is_usage_tracking_path_excluded(request_path):
        return

    resolver_match = getattr(request, "resolver_match", None)
    page_label = (
        getattr(resolver_match, "url_name", None)
        or getattr(resolver_match, "view_name", None)
        or getattr(resolver_match, "route", None)
        or getattr(request, "path", "")
    )
    page_label = str(page_label).replace("_", " ").strip() or str(getattr(request, "path", ""))

    track_indy_hub_usage_for_user(
        user,
        page_path=request_path,
        page_label=page_label,
    )
    request._indy_hub_usage_tracked = True
