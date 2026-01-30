# Industry-specific utility functions
"""
Industry-specific utility functions for the Indy Hub module.
These functions handle industry job calculations, production metrics, etc.
"""

from __future__ import annotations

# Standard Library
from datetime import datetime, timedelta
from typing import Any

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


def _coerce_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _utcnow_like(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return datetime.utcnow()
    return datetime.now(dt.tzinfo)


def calculate_industry_metrics(jobs_data):
    """
    Calculate industry performance metrics from job data.

    Args:
        jobs_data: List of industry job data dictionaries

    Returns:
        dict: Calculated metrics including completion rates, efficiency, etc.
    """
    if not jobs_data:
        return {
            "total_jobs": 0,
            "completed_jobs": 0,
            "completion_rate": 0,
            "average_duration": 0,
            "active_jobs": 0,
        }

    total_jobs = len(jobs_data)
    completed_statuses = {"delivered", "ready", "completed"}
    active_statuses = {"active"}

    completed_jobs = sum(
        1
        for job in jobs_data
        if str(job.get("status") or "").lower() in completed_statuses
    )
    active_jobs = sum(
        1
        for job in jobs_data
        if str(job.get("status") or "").lower() in active_statuses
    )
    completion_rate = (completed_jobs / total_jobs) if total_jobs > 0 else 0

    durations: list[int] = []
    for job in jobs_data:
        duration = _coerce_int(job.get("duration"), default=None)
        if duration is None:
            start = _coerce_datetime(job.get("start_date"))
            end = _coerce_datetime(job.get("end_date"))
            if start and end:
                duration = int(max((end - start).total_seconds(), 0))
        if duration is not None and duration >= 0:
            durations.append(duration)

    average_duration = int(round(sum(durations) / len(durations))) if durations else 0

    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "completion_rate": completion_rate,
        "average_duration": average_duration,
        "active_jobs": active_jobs,
    }


def optimize_production_chain(blueprint_data):
    """
    Analyze and suggest optimizations for production chains.

    Args:
        blueprint_data: List of blueprint data dictionaries

    Returns:
        dict: Optimization suggestions and recommendations
    """
    if not blueprint_data:
        return {"suggestions": [], "efficiency_score": 0}

    me_values: list[int] = []
    te_values: list[int] = []
    for entry in blueprint_data:
        if not isinstance(entry, dict):
            continue
        me = _coerce_int(
            entry.get("material_efficiency", entry.get("me")), default=None
        )
        te = _coerce_int(entry.get("time_efficiency", entry.get("te")), default=None)
        if me is not None:
            me_values.append(max(me, 0))
        if te is not None:
            te_values.append(max(te, 0))

    avg_me = (sum(me_values) / len(me_values)) if me_values else None
    avg_te = (sum(te_values) / len(te_values)) if te_values else None

    suggestions: list[str] = []
    # Use common EVE blueprint caps (ME 0-10, TE 0-20) as a heuristic.
    efficiency_score: int | None = None
    if avg_me is not None and avg_te is not None:
        me_score = min(max(avg_me, 0.0), 10.0) / 10.0
        te_score = min(max(avg_te, 0.0), 20.0) / 20.0
        efficiency_score = int(round((me_score * 50.0) + (te_score * 50.0)))
        if avg_me < 10:
            suggestions.append("Consider improving ME research")
        if avg_te < 20:
            suggestions.append("Consider improving TE research")
    elif avg_me is not None:
        me_score = min(max(avg_me, 0.0), 10.0) / 10.0
        efficiency_score = int(round(me_score * 100.0))
        if avg_me < 10:
            suggestions.append("Consider improving ME research")
    elif avg_te is not None:
        te_score = min(max(avg_te, 0.0), 20.0) / 20.0
        efficiency_score = int(round(te_score * 100.0))
        if avg_te < 20:
            suggestions.append("Consider improving TE research")
    else:
        efficiency_score = 0
        suggestions.append("No efficiency data available")

    return {"suggestions": suggestions, "efficiency_score": efficiency_score}


def calculate_production_costs(blueprint_id, runs=1):
    """
    Calculate production costs for a blueprint.

    Args:
        blueprint_id: ID of the blueprint
        runs: Number of production runs

    Returns:
        dict: Cost breakdown including materials, fees, etc.
    """
    # This helper needs a reliable price source and blueprint material list.
    # Returning zeros here is misleading; instead return an explicit "unavailable" payload.
    logger.warning(
        "calculate_production_costs is not available (blueprint_id=%s, runs=%s)",
        blueprint_id,
        runs,
    )
    return {
        "available": False,
        "material_cost": None,
        "facility_fees": None,
        "total_cost": None,
    }


def estimate_completion_time(job_data):
    """
    Estimate completion time for industry jobs.

    Args:
        job_data: Job data dictionary

    Returns:
        int: Estimated completion time in seconds
    """
    if not isinstance(job_data, dict):
        return 0

    end_date = _coerce_datetime(job_data.get("end_date"))
    if end_date:
        now = _utcnow_like(end_date)
        remaining = int((end_date - now).total_seconds())
        return max(remaining, 0)

    duration = _coerce_int(job_data.get("duration"), default=None)
    start_date = _coerce_datetime(job_data.get("start_date"))
    if duration is not None and start_date is not None:
        now = _utcnow_like(start_date)
        remaining = int(
            ((start_date + timedelta(seconds=duration)) - now).total_seconds()
        )
        return max(remaining, 0)

    return 0
