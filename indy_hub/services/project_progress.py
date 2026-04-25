"""Helpers for production-item progress tracking on craft project cards."""

from __future__ import annotations

# Standard Library
from collections import defaultdict

# Django
from django.utils.translation import gettext as _

from ..models import (
    IndustryActivityMixin,
    IndustryJob,
    ProductionProjectItem,
    SDEBlueprintActivityProduct,
)
from ..utils.eve import get_blueprint_product_type_id

TRACKABLE_JOB_ACTIVITY_IDS = (
    IndustryActivityMixin.ACTIVITY_MANUFACTURING,
    IndustryActivityMixin.ACTIVITY_REACTIONS,
    IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
)
IGNORED_JOB_STATUSES = {"cancelled", "reverted"}


def _get_project_production_items(project) -> list[ProductionProjectItem]:
    project_items = list(getattr(project, "items").all())
    return [
        item
        for item in project_items
        if item.is_selected
        and item.is_craftable
        and item.inclusion_mode == ProductionProjectItem.InclusionMode.PRODUCE
    ]


def _get_project_item_product_type_id(project_item: ProductionProjectItem) -> int:
    explicit_type_id = int(project_item.type_id or 0)
    if explicit_type_id > 0:
        return explicit_type_id

    fallback_type_id = get_blueprint_product_type_id(project_item.blueprint_type_id)
    if int(fallback_type_id or 0) > 0:
        return int(fallback_type_id)

    return int(project_item.blueprint_type_id or 0)


def _get_output_quantity_per_run_by_blueprint(
    blueprint_type_ids: set[int],
) -> dict[tuple[int, int], int]:
    if not blueprint_type_ids:
        return {}

    try:
        rows = SDEBlueprintActivityProduct.objects.filter(
            eve_type_id__in=blueprint_type_ids,
            activity_id__in=TRACKABLE_JOB_ACTIVITY_IDS,
        ).values_list("eve_type_id", "activity_id", "quantity")
    except Exception:
        return {}

    output_quantities: dict[tuple[int, int], int] = {}
    for blueprint_type_id, activity_id, quantity in rows:
        output_quantities[(int(blueprint_type_id), int(activity_id))] = max(
            1,
            int(quantity or 1),
        )
    return output_quantities


def _resolve_job_output_quantity_per_run(
    job: IndustryJob,
    output_quantities: dict[tuple[int, int], int],
) -> int:
    blueprint_type_id = int(job.blueprint_type_id or 0)
    activity_id = int(job.activity_id or 0)
    lookup_keys = [
        (blueprint_type_id, activity_id),
        (blueprint_type_id, IndustryActivityMixin.ACTIVITY_MANUFACTURING),
        (blueprint_type_id, IndustryActivityMixin.ACTIVITY_REACTIONS),
        (blueprint_type_id, IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY),
    ]
    for key in lookup_keys:
        quantity = output_quantities.get(key)
        if quantity:
            return max(1, int(quantity))
    return 1


def _serialize_job_candidate(
    job: IndustryJob,
    output_quantities: dict[tuple[int, int], int],
) -> dict[str, object]:
    output_quantity_per_run = _resolve_job_output_quantity_per_run(
        job, output_quantities
    )
    planned_runs = max(0, int(job.runs or 0))
    total_output_quantity = planned_runs * output_quantity_per_run

    successful_runs = job.successful_runs
    if successful_runs is None:
        successful_runs = planned_runs if job.is_completed else 0
    successful_runs = max(0, int(successful_runs or 0))
    if planned_runs:
        successful_runs = min(successful_runs, planned_runs)

    completed_output_quantity = (
        successful_runs * output_quantity_per_run if job.is_completed else 0
    )
    progress_percent = max(0, min(100, int(job.progress_percent or 0)))
    progress_output_quantity = completed_output_quantity
    if job.is_active and total_output_quantity > 0:
        progress_output_quantity = max(
            completed_output_quantity,
            int(round(total_output_quantity * (progress_percent / 100))),
        )

    status_label = _("Completed") if job.is_completed else str(job.status or "").title()
    return {
        "id": str(job.job_id),
        "job_id": int(job.job_id),
        "status": str(job.status or ""),
        "status_label": status_label,
        "activity_id": int(job.activity_id or 0),
        "activity_name": str(job.activity_name or ""),
        "character_name": str(job.character_name or ""),
        "location_name": str(job.location_name or ""),
        "product_type_id": int(job.product_type_id or 0),
        "product_type_name": str(job.product_type_name or ""),
        "blueprint_type_id": int(job.blueprint_type_id or 0),
        "blueprint_type_name": str(job.blueprint_type_name or ""),
        "runs": planned_runs,
        "successful_runs": successful_runs,
        "output_quantity_per_run": output_quantity_per_run,
        "total_output_quantity": total_output_quantity,
        "completed_output_quantity": completed_output_quantity,
        "progress_output_quantity": progress_output_quantity,
        "progress_percent": progress_percent,
        "is_active": bool(job.is_active),
        "is_completed": bool(job.is_completed),
    }


def _get_project_job_candidates(
    project,
    product_type_ids: set[int],
) -> dict[int, list[dict[str, object]]]:
    if not product_type_ids:
        return {}

    jobs = list(
        IndustryJob.objects.filter(
            owner_user=project.user,
            product_type_id__in=product_type_ids,
            activity_id__in=TRACKABLE_JOB_ACTIVITY_IDS,
        )
        .exclude(status__in=IGNORED_JOB_STATUSES)
        .order_by("-start_date", "-job_id")
    )
    output_quantities = _get_output_quantity_per_run_by_blueprint(
        {
            int(job.blueprint_type_id or 0)
            for job in jobs
            if int(job.blueprint_type_id or 0) > 0
        }
    )

    jobs_by_product_type_id: dict[int, list[dict[str, object]]] = defaultdict(list)
    for job in jobs:
        product_type_id = int(job.product_type_id or 0)
        if product_type_id <= 0:
            continue
        jobs_by_product_type_id[product_type_id].append(
            _serialize_job_candidate(job, output_quantities)
        )
    return dict(jobs_by_product_type_id)


def normalize_project_progress(
    project, progress_data: dict | None
) -> dict[str, object]:
    raw_progress = progress_data if isinstance(progress_data, dict) else {}
    production_items = _get_project_production_items(project)
    jobs_by_product_type_id = _get_project_job_candidates(
        project,
        {
            _get_project_item_product_type_id(item)
            for item in production_items
            if _get_project_item_product_type_id(item) > 0
        },
    )
    valid_ids = {str(item.id) for item in production_items}
    completed_ids = {
        str(activity_id)
        for activity_id in raw_progress.get("completed_ids", [])
        if str(activity_id) in valid_ids
    }
    in_progress_ids = {
        str(activity_id)
        for activity_id in raw_progress.get("in_progress_ids", [])
        if str(activity_id) in valid_ids and str(activity_id) not in completed_ids
    }
    raw_job_links = raw_progress.get("linked_job_ids_by_item")
    linked_job_ids_by_item_source = (
        raw_job_links if isinstance(raw_job_links, dict) else {}
    )

    items = []
    normalized_job_links: dict[str, list[str]] = {}
    for project_item in production_items:
        item_id = str(project_item.id)
        quantity_requested = max(0, int(project_item.quantity_requested or 0))
        product_type_id = _get_project_item_product_type_id(project_item)
        candidate_jobs = jobs_by_product_type_id.get(product_type_id, [])
        valid_job_ids = {str(job["job_id"]) for job in candidate_jobs}
        linked_job_ids = sorted(
            {
                str(job_id)
                for job_id in linked_job_ids_by_item_source.get(item_id, [])
                if str(job_id) in valid_job_ids
            }
        )
        if linked_job_ids:
            normalized_job_links[item_id] = linked_job_ids

        linked_jobs = []
        auto_completed_quantity_raw = 0
        auto_progress_quantity_raw = 0
        has_active_linked_job = False
        for candidate_job in candidate_jobs:
            is_linked = str(candidate_job["job_id"]) in linked_job_ids
            job_payload = {**candidate_job, "is_linked": is_linked}
            if is_linked:
                linked_jobs.append(job_payload)
                auto_completed_quantity_raw += int(
                    candidate_job["completed_output_quantity"] or 0
                )
                auto_progress_quantity_raw += int(
                    candidate_job["progress_output_quantity"] or 0
                )
                has_active_linked_job = has_active_linked_job or bool(
                    candidate_job["is_active"]
                )

        auto_completed_quantity = min(quantity_requested, auto_completed_quantity_raw)
        auto_progress_quantity = min(
            quantity_requested,
            max(auto_completed_quantity, auto_progress_quantity_raw),
        )
        has_linked_jobs = bool(linked_job_ids)
        manual_is_completed = item_id in completed_ids
        manual_is_in_progress = item_id in in_progress_ids
        is_completed = (
            auto_completed_quantity >= quantity_requested
            if has_linked_jobs and quantity_requested > 0
            else manual_is_completed
        )
        if has_linked_jobs and quantity_requested == 0:
            is_completed = bool(linked_job_ids)
        is_in_progress = (
            (not is_completed and has_active_linked_job)
            if has_linked_jobs
            else (manual_is_in_progress and not manual_is_completed)
        )
        completed_quantity = (
            auto_completed_quantity
            if has_linked_jobs
            else (quantity_requested if manual_is_completed else 0)
        )
        progress_quantity = (
            auto_progress_quantity if has_linked_jobs else completed_quantity
        )
        items.append(
            {
                "id": item_id,
                "type_id": product_type_id,
                "type_name": project_item.type_name,
                "quantity_requested": quantity_requested,
                "blueprint_type_id": int(project_item.blueprint_type_id or 0),
                "is_in_progress": is_in_progress,
                "is_completed": is_completed,
                "completed_quantity": completed_quantity,
                "progress_quantity": progress_quantity,
                "manual_is_in_progress": manual_is_in_progress,
                "manual_is_completed": manual_is_completed,
                "linked_job_ids": linked_job_ids,
                "linked_job_count": len(linked_job_ids),
                "available_jobs": [
                    {
                        **candidate_job,
                        "is_linked": str(candidate_job["job_id"]) in linked_job_ids,
                    }
                    for candidate_job in candidate_jobs
                ],
                "linked_jobs": linked_jobs,
                "auto_completed_quantity": auto_completed_quantity,
                "auto_progress_quantity": auto_progress_quantity,
            }
        )

    total_count = len(items)
    total_quantity = sum(max(0, int(item["quantity_requested"] or 0)) for item in items)
    completed_count = sum(1 for item in items if item["is_completed"])
    in_progress_count = sum(1 for item in items if item["is_in_progress"])
    completed_quantity = sum(
        max(0, int(item["completed_quantity"] or 0)) for item in items
    )
    progress_quantity = sum(
        max(0, int(item["progress_quantity"] or 0)) for item in items
    )
    completion_percentage = (
        int(round((progress_quantity / total_quantity) * 100)) if total_quantity else 0
    )

    return {
        "items": items,
        "in_progress_ids": sorted(in_progress_ids),
        "completed_ids": sorted(completed_ids),
        "linked_job_ids_by_item": normalized_job_links,
        "total_count": total_count,
        "total_quantity": total_quantity,
        "completed_count": completed_count,
        "completed_quantity": completed_quantity,
        "progress_quantity": progress_quantity,
        "in_progress_count": in_progress_count,
        "completion_percentage": completion_percentage,
        "has_started": bool(completed_ids or in_progress_ids or normalized_job_links),
    }


def update_project_summary_progress(
    summary: dict | None,
    project,
    *,
    in_progress_ids: list[str] | tuple[str, ...],
    completed_ids: list[str] | tuple[str, ...],
    linked_job_ids_by_item: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    next_summary = dict(summary or {})
    normalized = normalize_project_progress(
        project,
        {
            "in_progress_ids": list(in_progress_ids or []),
            "completed_ids": list(completed_ids or []),
            "linked_job_ids_by_item": dict(linked_job_ids_by_item or {}),
        },
    )
    next_summary["item_progress"] = {
        "in_progress_ids": normalized["in_progress_ids"],
        "completed_ids": normalized["completed_ids"],
        "linked_job_ids_by_item": normalized["linked_job_ids_by_item"],
        "total_count": normalized["total_count"],
        "total_quantity": normalized["total_quantity"],
        "completed_count": normalized["completed_count"],
        "completed_quantity": normalized["completed_quantity"],
        "progress_quantity": normalized["progress_quantity"],
        "in_progress_count": normalized["in_progress_count"],
        "completion_percentage": normalized["completion_percentage"],
    }
    return next_summary
