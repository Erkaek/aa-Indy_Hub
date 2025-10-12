# Industry-related views
# Standard Library
import json
import logging
from collections import defaultdict
from decimal import Decimal
from math import ceil

# Third Party
import requests

# Django
from django.conf import settings

if "eveuniverse" in getattr(settings, "INSTALLED_APPS", ()):  # pragma: no branch
    try:  # pragma: no cover - EveUniverse optional
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType
    except ImportError:  # pragma: no cover - fallback when EveUniverse absent
        EveType = None
else:  # pragma: no cover - EveUniverse not installed
    EveType = None
# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# AA Example App
from indy_hub.models import CharacterSettings

from ..decorators import indy_hub_access_required
from ..models import (
    Blueprint,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    IndustryJob,
    ProductionConfig,
    ProductionSimulation,
)
from ..notifications import notify_user
from ..tasks.industry import update_blueprints_for_user, update_industry_jobs_for_user
from ..utils.eve import get_character_name, get_type_name

logger = logging.getLogger(__name__)


# --- Blueprint and job views ---
@indy_hub_access_required
@login_required
def personnal_bp_list(request):
    # Copy of the old blueprints_list code
    try:
        # Check if we need to sync data
        force_update = request.GET.get("refresh") == "1"
        if force_update:
            logger.info(
                f"User {request.user.username} requested blueprint refresh; enqueuing Celery task"
            )
            # Removed tracking update since unified settings don't track refresh times
            update_blueprints_for_user.delay(request.user.id)
    except Exception as e:
        logger.error(f"Error handling blueprint refresh: {e}")
        messages.error(request, f"Error handling blueprint refresh: {e}")
    search = request.GET.get("search", "")
    efficiency_filter = request.GET.get("efficiency", "")
    type_filter = request.GET.get("type", "")
    character_filter = request.GET.get("character", "")
    activity_id = request.GET.get("activity_id", "")
    sort_order = request.GET.get("order", "asc")
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 25))

    # Determine which activity IDs to include based on filter
    # Determine which activity IDs to include based on filter
    if activity_id == "1":
        filter_ids = [1]
    elif activity_id == "9,11":
        # Both IDs represent Reactions
        filter_ids = [9, 11]
    else:
        # All activities: manufacturing (1) and reactions (9,11)
        filter_ids = [1, 9, 11]
    try:
        # Fetch allowed type IDs for the selected activities
        id_list = ",".join(str(i) for i in filter_ids)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT eve_type_id
                FROM eveuniverse_eveindustryactivityproduct
                WHERE activity_id IN ({id_list})
                """
            )
            allowed_type_ids = [row[0] for row in cursor.fetchall()]
        blueprints_qs = Blueprint.objects.filter(
            owner_user=request.user, type_id__in=allowed_type_ids
        )
        if search:
            blueprints_qs = blueprints_qs.filter(
                Q(type_name__icontains=search) | Q(type_id__icontains=search)
            )
        if efficiency_filter == "perfect":
            blueprints_qs = blueprints_qs.filter(
                material_efficiency__gte=10, time_efficiency__gte=20
            )
        elif efficiency_filter == "researched":
            blueprints_qs = blueprints_qs.filter(
                Q(material_efficiency__gt=0) | Q(time_efficiency__gt=0)
            )
        elif efficiency_filter == "unresearched":
            blueprints_qs = blueprints_qs.filter(
                material_efficiency=0, time_efficiency=0
            )
        if type_filter == "original":
            blueprints_qs = blueprints_qs.filter(
                bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION]
            )
        elif type_filter == "copy":
            blueprints_qs = blueprints_qs.filter(bp_type=Blueprint.BPType.COPY)
        if character_filter:
            blueprints_qs = blueprints_qs.filter(character_id=character_filter)
        blueprints_qs = blueprints_qs.order_by("type_name")
        # Group identical items by type, ME, TE; compute normalized quantities & runs
        bp_items = []
        grouped = {}

        def normalized_quantity(value):
            if value in (-1, -2):
                return 1
            if value is None:
                return 0
            return max(value, 0)

        total_original_quantity = 0
        total_copy_quantity = 0
        total_quantity = 0

        for bp in blueprints_qs:
            quantity_value = normalized_quantity(bp.quantity)
            total_quantity += quantity_value

            if bp.is_copy:
                category = "copy"
                total_copy_quantity += quantity_value
            else:
                category = "reaction" if bp.is_reaction else "original"
                total_original_quantity += quantity_value

            key = (bp.type_id, bp.material_efficiency, bp.time_efficiency, category)
            if key not in grouped:
                bp.orig_quantity = 0
                bp.copy_quantity = 0
                bp.total_quantity = 0
                bp.total_runs = 0
                grouped[key] = bp
                bp_items.append(bp)

            agg = grouped[key]
            if category == "copy":
                agg.copy_quantity += quantity_value
                agg.total_runs += (bp.runs or 0) * max(quantity_value, 1)
            else:
                agg.orig_quantity += quantity_value

            agg.total_quantity = agg.orig_quantity + agg.copy_quantity
            agg.runs = agg.total_runs
        # Compute precise human-readable location path for each blueprint
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveStation, EveStructure
        except (ImportError, RuntimeError, LookupError):
            EveStation = None
            EveStructure = None

        def get_location_path(location_id):
            if EveStation:
                try:
                    st = EveStation.objects.get(id=location_id)
                    sys = st.solar_system
                    cons = sys.constellation
                    reg = cons.region
                    return f"{reg.name} > {cons.name} > {sys.name} > {st.name}"
                except EveStation.DoesNotExist:
                    pass
            if EveStructure:
                try:
                    struct = EveStructure.objects.get(id=location_id)
                    sys = struct.solar_system
                    cons = sys.constellation
                    reg = cons.region
                    return f"{reg.name} > {cons.name} > {sys.name} > {struct.name}"
                except EveStructure.DoesNotExist:
                    pass
            return None

        for bp in bp_items:
            path = get_location_path(bp.location_id)
            bp.location_path = path if path else bp.location_flag

        paginator = Paginator(bp_items, per_page)
        blueprints_page = paginator.get_page(page)
        total_blueprints = total_quantity
        originals_count = total_original_quantity
        copies_count = total_copy_quantity
        character_ids = (
            Blueprint.objects.filter(owner_user=request.user)
            .values_list("character_id", flat=True)
            .distinct()
        )
        character_map = {cid: get_character_name(cid) for cid in character_ids}
        # Removed update status tracking since unified settings don't track this

        # Apply consistent activity labels
        activity_labels = {
            1: "Manufacturing",
            3: "TE Research",
            4: "ME Research",
            5: "Copying",
            8: "Invention",
            9: "Reactions",
            11: "Reactions",
        }
        # Build grouped activity options: All, Manufacturing, Reactions
        activity_options = [
            ("", "All Activities"),
            ("1", activity_labels[1]),
            ("9,11", activity_labels[9]),
        ]
        context = {
            "blueprints": blueprints_page,
            "statistics": {
                "total_count": total_blueprints,
                "original_count": originals_count,
                "copy_count": copies_count,
                "perfect_me_count": blueprints_qs.filter(
                    material_efficiency__gte=10
                ).count(),
                "perfect_te_count": blueprints_qs.filter(
                    time_efficiency__gte=20
                ).count(),
                "character_count": len(character_ids),
                "character_ids": character_ids,
            },
            "current_filters": {
                "search": search,
                "efficiency": efficiency_filter,
                "type": type_filter,
                "character": character_filter,
                "activity_id": activity_id,
                "sort": request.GET.get("sort", "type_name"),
                "order": sort_order,
                "per_page": per_page,
            },
            "per_page_options": [10, 25, 50, 100, 200],
            "activity_options": activity_options,
            # List of character IDs for filter dropdown
            "character_ids": character_ids,
            "character_map": character_map,
        }
        return render(request, "indy_hub/Personnal_BP_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying blueprints: {e}")
        messages.error(request, f"Error displaying blueprints: {e}")
        return redirect("indy_hub:index")


@indy_hub_access_required
@login_required
def all_bp_list(request):
    search = request.GET.get("search", "").strip()
    activity_id = request.GET.get("activity_id", "")
    market_group_id = request.GET.get("market_group_id", "")

    # Base SQL
    sql = (
        "SELECT t.id, t.name "
        "FROM eveuniverse_evetype t "
        "JOIN eveuniverse_eveindustryactivityproduct a ON t.id = a.eve_type_id "
        "WHERE t.published = 1"
    )
    # Append activity filter
    if activity_id == "1":
        sql += " AND a.activity_id = 1"
    elif activity_id == "reactions":
        sql += " AND a.activity_id IN (9, 11)"
    else:
        sql += " AND a.activity_id IN (1, 9, 11)"
    # Params for search and market_group filters
    params = []
    if search:
        sql += " AND (t.name LIKE %s OR t.id LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    if market_group_id:
        sql += " AND t.eve_group_id = %s"
        params.append(market_group_id)
    sql += " ORDER BY t.name ASC"
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 25))
    # Initial empty pagination before fetching data
    paginator = Paginator([], per_page)
    blueprints_page = paginator.get_page(page)
    # Fetch raw activity options for activity dropdown
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name FROM eveuniverse_eveindustryactivity WHERE id IN (1,9,11) ORDER BY id"
        )
        raw_activity_options = cursor.fetchall()
    # Apply consistent activity labels
    activity_labels = {
        1: "Manufacturing",
        3: "TE Research",
        4: "ME Research",
        5: "Copying",
        8: "Invention",
        9: "Reactions",
        11: "Reactions",
    }
    # Build grouped activity options: All, Manufacturing, Reactions
    raw_ids = [opt[0] for opt in raw_activity_options]
    activity_options = [("", "All Activities")]
    # Manufacturing
    activity_options.append(("1", activity_labels[1]))
    # Reactions group
    if any(r in raw_ids for r in [9, 11]):
        activity_options.append(("reactions", activity_labels[9]))
    blueprints = [
        {
            "type_id": row[0],
            "type_name": row[1],
        }
        for row in []
    ]
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            blueprints = [
                {
                    "type_id": row[0],
                    "type_name": row[1],
                }
                for row in cursor.fetchall()
            ]
        paginator = Paginator(blueprints, per_page)
        blueprints_page = paginator.get_page(page)
        # Fetch market group options based on all matching blueprints, not just current page
        with connection.cursor() as cursor:
            type_ids = [bp["type_id"] for bp in blueprints]
            if type_ids:
                placeholders = ",".join(["%s"] * len(type_ids))
                query = f"""
                    SELECT DISTINCT t.eve_group_id, g.name
                    FROM eveuniverse_evetype t
                    JOIN eveuniverse_evegroup g ON t.eve_group_id = g.id
                    WHERE t.eve_group_id IS NOT NULL
                        AND t.id IN ({placeholders})
                    ORDER BY g.name
                """
                cursor.execute(query, type_ids)
                market_group_options = [(row[0], row[1]) for row in cursor.fetchall()]
            else:
                market_group_options = []
    except Exception as e:
        logger.error(f"Error fetching blueprints: {e}")
        messages.error(request, f"Error fetching blueprints: {e}")
    return render(
        request,
        "indy_hub/All_BP_list.html",
        {
            "blueprints": blueprints_page,
            "filters": {
                "search": search,
                "activity_id": activity_id,
                "market_group_id": market_group_id,
            },
            "activity_options": activity_options,
            # Pass market group ID/name pairs for dropdown
            "market_group_options": market_group_options,
            "per_page_options": [10, 25, 50, 100, 200],
        },
    )


@indy_hub_access_required
@login_required
def personnal_job_list(request):
    try:
        force_update = request.GET.get("refresh") == "1"
        if force_update:
            logger.info(
                f"User {request.user.username} requested jobs refresh; enqueuing Celery task"
            )
            # Removed last_refresh_request tracking since unified settings don't track this
            update_industry_jobs_for_user.delay(request.user.id)
    except Exception as e:
        logger.error(f"Error handling jobs refresh: {e}")
        messages.error(request, f"Error handling jobs refresh: {e}")
    search = request.GET.get("search", "")
    status_filter = request.GET.get("status", "")
    activity_filter = request.GET.get("activity", "")
    character_filter = request.GET.get("character", "")
    sort_by = request.GET.get("sort", "start_date")
    sort_order = request.GET.get("order", "desc")
    page = int(request.GET.get("page", 1))
    per_page = request.GET.get("per_page")
    if per_page:
        per_page = int(per_page)
        if per_page < 1:
            per_page = 1
    else:
        per_page = IndustryJob.objects.filter(owner_user=request.user).count()
        if per_page < 1:
            per_page = 1
    jobs_qs = IndustryJob.objects.filter(owner_user=request.user)
    # Base queryset for this user
    base_jobs_qs = IndustryJob.objects.filter(owner_user=request.user)
    jobs_qs = base_jobs_qs
    now = timezone.now()
    all_character_ids = list(jobs_qs.values_list("character_id", flat=True).distinct())
    character_map = (
        {cid: get_character_name(cid) for cid in all_character_ids}
        if all_character_ids
        else {}
    )
    try:
        if search:
            job_id_q = Q(job_id__icontains=search) if search.isdigit() else Q()
            char_name_ids = [
                cid
                for cid, name in character_map.items()
                if name and search.lower() in name.lower()
            ]
            char_name_q = Q(character_id__in=char_name_ids) if char_name_ids else Q()
            jobs_qs = jobs_qs.filter(
                Q(blueprint_type_name__icontains=search)
                | Q(product_type_name__icontains=search)
                | Q(activity_name__icontains=search)
                | job_id_q
                | char_name_q
            )
        if status_filter:
            status_filter = status_filter.strip().lower()
            if status_filter == "active":
                jobs_qs = jobs_qs.filter(status="active", end_date__gt=now)
            elif status_filter == "completed":
                jobs_qs = jobs_qs.filter(end_date__lte=now)
        if activity_filter:
            try:
                activity_ids = {
                    int(part.strip())
                    for part in str(activity_filter).split(",")
                    if part.strip()
                }
                if activity_ids:
                    jobs_qs = jobs_qs.filter(activity_id__in=activity_ids)
            except (TypeError, ValueError):
                logger.warning(
                    "[JOBS FILTER] Invalid activity filter value: '%s'",
                    activity_filter,
                )
        if character_filter:
            try:
                character_filter_int = int(character_filter.strip())
                jobs_qs = jobs_qs.filter(character_id=character_filter_int)
            except (ValueError, TypeError):
                logger.warning(
                    f"[JOBS FILTER] Invalid character_filter value: '{character_filter}'"
                )
                pass
        if sort_order == "desc":
            sort_by = f"-{sort_by}"
        jobs_qs = jobs_qs.order_by(sort_by)
        paginator = Paginator(jobs_qs, per_page)
        jobs_page = paginator.get_page(page)
        total_jobs = jobs_qs.count()
        active_jobs = jobs_qs.filter(status="active", end_date__gt=now).count()
        completed_jobs = jobs_qs.filter(end_date__lte=now).count()
        statistics = {
            "total": total_jobs,
            "active": active_jobs,
            "completed": completed_jobs,
        }
        # Only show computed statuses for filtering: 'active' and 'completed'
        statuses = ["active", "completed"]
        # Static mapping for activity filter with labels
        activity_labels = {
            1: "Manufacturing",
            3: "TE Research",
            4: "ME Research",
            5: "Copying",
            8: "Invention",
            9: "Reactions",
        }
        # Include only activities from base jobs (unfiltered) for filter options
        present_ids = base_jobs_qs.values_list("activity_id", flat=True).distinct()
        activities = [
            (str(aid), activity_labels.get(aid, str(aid))) for aid in present_ids
        ]
        # Removed update status tracking since unified settings don't track this
        jobs_on_page = list(jobs_page.object_list)
        blueprint_ids = [job.blueprint_id for job in jobs_on_page if job.blueprint_id]
        blueprint_map = {
            bp.item_id: bp
            for bp in Blueprint.objects.filter(
                owner_user=request.user, item_id__in=blueprint_ids
            )
        }

        activity_definitions = [
            {
                "key": "manufacturing",
                "activity_ids": {1},
                "title": _("Manufacturing"),
                "subtitle": _("Mass-produce items and hulls for your hangars."),
                "icon": "fas fa-industry",
                "chip": "MFG",
                "badge_variant": "bg-primary",
            },
            {
                "key": "research_te",
                "activity_ids": {3},
                "title": _("Time Efficiency Research"),
                "subtitle": _("Improve blueprint TE levels to reduce job durations."),
                "icon": "fas fa-stopwatch",
                "chip": "TE",
                "badge_variant": "bg-info",
            },
            {
                "key": "research_me",
                "activity_ids": {4},
                "title": _("Material Efficiency Research"),
                "subtitle": _("Raise ME levels to save materials on future builds."),
                "icon": "fas fa-flask",
                "chip": "ME",
                "badge_variant": "bg-success",
            },
            {
                "key": "copying",
                "activity_ids": {5},
                "title": _("Copying"),
                "subtitle": _(
                    "Generate blueprint copies ready for production or invention."
                ),
                "icon": "fas fa-copy",
                "chip": "COPY",
                "badge_variant": "bg-warning",
            },
            {
                "key": "invention",
                "activity_ids": {8},
                "title": _("Invention"),
                "subtitle": _(
                    "Transform tech I copies into advanced tech II blueprints."
                ),
                "icon": "fas fa-bolt",
                "chip": "INV",
                "badge_variant": "bg-dark",
            },
            {
                "key": "reactions",
                "activity_ids": {9, 11},
                "title": _("Reactions"),
                "subtitle": _(
                    "Process raw materials through biochemical and polymer reactions."
                ),
                "icon": "fas fa-vials",
                "chip": "RX",
                "badge_variant": "bg-danger",
            },
            {
                "key": "other",
                "activity_ids": set(),
                "title": _("Other Activities"),
                "subtitle": _(
                    "Specialised jobs that fall outside the main categories."
                ),
                "icon": "fas fa-tools",
                "chip": _("Other"),
                "badge_variant": "bg-secondary",
            },
        ]

        activity_meta_by_key = {meta["key"]: meta for meta in activity_definitions}
        activity_key_by_id = {}
        for meta in activity_definitions:
            for aid in meta["activity_ids"]:
                activity_key_by_id[aid] = meta["key"]

        grouped_jobs = defaultdict(list)

        for job in jobs_on_page:
            activity_key = activity_key_by_id.get(job.activity_id, "other")
            activity_meta = activity_meta_by_key[activity_key]
            setattr(job, "activity_meta", activity_meta)
            setattr(
                job,
                "display_character_name",
                character_map.get(job.character_id, job.character_id),
            )
            status_label = _("Completed") if job.is_completed else job.status.title()
            setattr(job, "status_label", status_label)
            setattr(job, "probability_percent", None)
            if job.probability is not None:
                try:
                    setattr(job, "probability_percent", round(job.probability * 100, 1))
                except TypeError:
                    setattr(job, "probability_percent", None)

            blueprint = blueprint_map.get(job.blueprint_id)
            research_details = None
            runs_count = job.runs or 0
            if job.activity_id in {3, 4}:
                if job.activity_id == 3:
                    current_value = blueprint.time_efficiency if blueprint else None
                    max_value = 20
                    attr_label = "TE"
                    per_run_gain = 2
                else:
                    current_value = blueprint.material_efficiency if blueprint else None
                    max_value = 10
                    attr_label = "ME"
                    per_run_gain = 1

                runs_count = max(runs_count, 0)
                completed_runs = job.successful_runs or 0
                if completed_runs < 0:
                    completed_runs = 0
                if runs_count:
                    completed_runs = min(completed_runs, runs_count)

                total_potential_gain = runs_count * per_run_gain

                base_value = None
                target_value = None
                effective_gain = total_potential_gain

                if current_value is not None:
                    inferred_start = current_value - (completed_runs * per_run_gain)
                    base_value = max(0, min(max_value, inferred_start))
                    projected_target = base_value + total_potential_gain
                    target_value = min(max_value, projected_target)
                    effective_gain = max(0, target_value - base_value)

                research_details = {
                    "attribute": attr_label,
                    "base": base_value,
                    "target": target_value,
                    "increments": runs_count,
                    "level_gain": effective_gain,
                    "max": max_value,
                }
            setattr(job, "research_details", research_details)

            copy_details = None
            if job.activity_id == 5:
                copy_details = {
                    "runs": job.runs,
                    "licensed_runs": job.licensed_runs,
                }
            setattr(job, "copy_details", copy_details)

            setattr(
                job,
                "output_name",
                job.product_type_name or job.product_type_id,
            )
            grouped_jobs[activity_key].append(job)

        job_groups = [
            {
                "key": meta["key"],
                "title": meta["title"],
                "subtitle": meta["subtitle"],
                "icon": meta["icon"],
                "chip": meta["chip"],
                "badge_variant": meta["badge_variant"],
                "jobs": grouped_jobs.get(meta["key"], []),
            }
            for meta in activity_definitions
            if grouped_jobs.get(meta["key"])
        ]

        context = {
            "jobs": jobs_page,
            "statistics": statistics,
            "character_ids": all_character_ids,
            "statuses": statuses,
            "activities": activities,
            "current_filters": {
                "search": search,
                "status": status_filter,
                "activity": activity_filter,
                "character": character_filter,
                "sort": request.GET.get("sort", "start_date"),
                "order": sort_order,
                "per_page": per_page,
            },
            "per_page_options": [10, 25, 50, 100, 200],
            "character_map": character_map,
            "jobs_page": jobs_page,
            "job_groups": job_groups,
            "has_job_results": bool(job_groups),
        }
        # progress_percent and display_eta now available via model properties in template
        return render(request, "indy_hub/Personnal_Job_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying industry jobs: {e}")
        messages.error(request, f"Error displaying industry jobs: {e}")
        return redirect("indy_hub:index")


def collect_blueprints_with_level(blueprint_configs):
    """
    Annoter chaque blueprint config avec un attribut 'level' correspondant à la profondeur maximale de l'arbre de matériaux.
    """
    # Mapping type_id -> blueprint config pour accès rapide
    config_map = {bc["type_id"]: bc for bc in blueprint_configs}

    def get_level(type_id):
        bc = config_map.get(type_id)
        if bc is None:
            return 0
        # Si déjà calculé, on retourne la valeur
        if bc.get("level") is not None:
            return bc["level"]
        # Récupère les enfants (matériaux), ou liste vide si non défini
        children = (
            [m["type_id"] for m in bc.get("materials", [])] if "materials" in bc else []
        )
        # Calcul récursif du niveau
        level = 1 + max((get_level(child_id) for child_id in children), default=0)
        bc["level"] = level
        return level

    # Calcul du niveau pour chaque blueprint
    for bc in blueprint_configs:
        get_level(bc["type_id"])
    return blueprint_configs


@indy_hub_access_required
@login_required
def craft_bp(request, type_id):

    # --- Paramètres de la requête ---
    try:
        num_runs = int(request.GET.get("runs", 1))
        if num_runs < 1:
            num_runs = 1
    except Exception:
        num_runs = 1

    try:
        me = int(request.GET.get("me", 0))
    except ValueError:
        me = 0
    try:
        te = int(request.GET.get("te", 0))
    except ValueError:
        te = 0
    me = max(0, min(me, 10))
    te = max(0, min(te, 20))

    # --- Récupération de l'onglet actif depuis les paramètres ---
    active_tab = request.GET.get("active_tab", "materials")  # Par défaut Materials

    # --- Récupération des décisions buy/craft depuis les paramètres de la requête ---
    buy_decisions = set()
    buy_list = request.GET.get("buy", "")
    if buy_list:
        try:
            # Parse comma-separated list of type_ids to buy instead of craft
            buy_decisions = {
                int(tid.strip()) for tid in buy_list.split(",") if tid.strip().isdigit()
            }
            logger.info(f"Buy decisions parsed: {buy_decisions}")  # Debug log
        except ValueError:
            buy_decisions = set()
    else:
        logger.info("No buy decisions found in URL parameters")  # Debug log

    try:
        # --- Récupération du nom du blueprint ---
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM eveuniverse_evetype WHERE id=%s", [type_id]
            )
            row = cursor.fetchone()
            bp_name = row[0] if row else str(type_id)

        # --- Récupération du produit final et quantité ---
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT product_eve_type_id, quantity
                FROM eveuniverse_eveindustryactivityproduct
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                LIMIT 1
                """,
                [type_id],
            )
            product_row = cursor.fetchone()
            product_type_id = product_row[0] if product_row else None
            output_qty_per_run = (
                product_row[1] if product_row and len(product_row) > 1 else 1
            )
            final_product_qty = output_qty_per_run * num_runs

        # --- Construction de l'arbre des matériaux ---
        def get_materials_tree(
            bp_id, runs, blueprint_me=0, depth=0, max_depth=10, seen=None
        ):
            """Construit récursivement l'arbre des matériaux pour un blueprint donné."""
            if seen is None:
                seen = set()
            if depth > max_depth or bp_id in seen:
                return []
            seen.add(bp_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT m.material_eve_type_id, t.name, m.quantity
                    FROM eveuniverse_eveindustryactivitymaterial m
                    JOIN eveuniverse_evetype t ON m.material_eve_type_id = t.id
                    WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                    """,
                    [bp_id],
                )
                mats = []
                for row in cursor.fetchall():
                    base_qty = row[2] * runs
                    # Utiliser le ME spécifique au blueprint et arrondir à l'unité supérieure
                    # Standard Library

                    qty = ceil(base_qty * (100 - blueprint_me) / 100)
                    mat = {
                        "type_id": row[0],
                        "type_name": row[1],
                        "quantity": qty,
                        # Default values, will be overwritten if blueprint exists
                        "cycles": None,
                        "produced_per_cycle": None,
                        "total_produced": None,
                        "surplus": None,
                    }
                    # Check if this material can be produced by a blueprint (i.e. is a sub-product)
                    with connection.cursor() as sub_cursor:
                        sub_cursor.execute(
                            """
                            SELECT eve_type_id
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [mat["type_id"]],
                        )
                        sub_bp_row = sub_cursor.fetchone()
                        if sub_bp_row:
                            sub_bp_id = sub_bp_row[0]
                            sub_cursor.execute(
                                """
                                SELECT quantity
                                FROM eveuniverse_eveindustryactivityproduct
                                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                                LIMIT 1
                                """,
                                [sub_bp_id],
                            )
                            prod_qty_row = sub_cursor.fetchone()
                            output_qty = prod_qty_row[0] if prod_qty_row else 1
                            # Standard Library

                            cycles = ceil(mat["quantity"] / output_qty)
                            total_produced = cycles * output_qty
                            surplus = total_produced - mat["quantity"]
                            mat["cycles"] = cycles
                            mat["produced_per_cycle"] = output_qty
                            mat["total_produced"] = total_produced
                            mat["surplus"] = surplus
                            mat["sub_materials"] = get_materials_tree(
                                sub_bp_id, cycles, 0, depth + 1, max_depth, seen.copy()
                            )
                        else:
                            mat["sub_materials"] = []
                    mats.append(mat)
            return mats

        materials_tree = get_materials_tree(type_id, num_runs, me)

        # --- Fonction pour collecter tous les blueprints à exclure des configs ---
        def collect_buy_exclusions(tree, buy_set, excluded=None):
            """
            Collecte tous les blueprint type_ids qui doivent être exclus des blueprints config.
            Si un item est marqué pour achat, le blueprint qui le produit et tous ses enfants sont exclus.
            """
            if excluded is None:
                excluded = set()

            for mat in tree:
                # Si ce matériau est marqué pour achat au lieu d'être produit
                if mat["type_id"] in buy_set:
                    # Trouver le blueprint qui produit ce matériau et l'exclure
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT eve_type_id
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [mat["type_id"]],
                        )
                        bp_row = cursor.fetchone()
                        if bp_row:
                            excluded.add(
                                bp_row[0]
                            )  # Exclure le blueprint qui produit cet item

                    # Récursivement exclure tous les blueprints enfants
                    if mat.get("sub_materials"):
                        collect_all_descendant_blueprints(
                            mat["sub_materials"], excluded
                        )
                elif mat.get("sub_materials"):
                    # Continuer la recherche dans les enfants
                    collect_buy_exclusions(mat["sub_materials"], buy_set, excluded)

            return excluded

        def collect_all_descendant_blueprints(tree, excluded):
            """Collecte récursivement tous les blueprints descendants d'un arbre."""
            for mat in tree:
                # Trouver le blueprint qui produit ce matériau
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat["type_id"]],
                    )
                    bp_row = cursor.fetchone()
                    if bp_row:
                        excluded.add(bp_row[0])

                if mat.get("sub_materials"):
                    collect_all_descendant_blueprints(mat["sub_materials"], excluded)

        # Collecter les exclusions basées sur les décisions buy/craft
        blueprint_exclusions = collect_buy_exclusions(materials_tree, buy_decisions)
        logger.info(f"Blueprint exclusions: {blueprint_exclusions}")  # Debug log

        def flatten_materials(materials, buy_as_final=None):
            """Recursively flatten the materials tree into a flat list of terminal inputs.

            Only leaf materials (or those explicitly marked for purchase) are retained so that
            the resulting list reflects the final resources required to complete the build.
            """
            # Standard Library
            from collections import defaultdict

            if buy_as_final is None:
                buy_as_final = set()

            def _flatten(mats, accumulator):
                for m in mats:
                    sub_items = m.get("sub_materials") or []
                    should_expand = bool(sub_items) and m["type_id"] not in buy_as_final

                    if not should_expand:
                        accumulator[m["type_id"]]["type_name"] = m["type_name"]
                        accumulator[m["type_id"]]["quantity"] += m["quantity"]

                    if should_expand:
                        _flatten(sub_items, accumulator)

            material_accumulator = defaultdict(lambda: {"type_name": "", "quantity": 0})
            _flatten(materials, material_accumulator)

            return [
                {
                    "type_id": type_id,
                    "type_name": data["type_name"],
                    "quantity": ceil(data["quantity"]),
                }
                for type_id, data in material_accumulator.items()
            ]

        # --- Extraction de tous les blueprints impliqués (racine + enfants) ---
        def extract_all_blueprint_type_ids(bp_id, acc=None, depth=0, max_depth=10):
            """Récupère récursivement tous les type_id de blueprints (racine + enfants)."""
            if acc is None:
                acc = set()
            if depth > max_depth or bp_id in acc:
                return acc
            acc.add(bp_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT m.material_eve_type_id
                    FROM eveuniverse_eveindustryactivitymaterial m
                    WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                    """,
                    [bp_id],
                )
                material_type_ids = [row[0] for row in cursor.fetchall()]
            for mat_type_id in material_type_ids:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat_type_id],
                    )
                    sub_bp_row = cursor.fetchone()
                    if sub_bp_row:
                        sub_bp_id = sub_bp_row[0]
                        extract_all_blueprint_type_ids(
                            sub_bp_id, acc, depth + 1, max_depth
                        )
            return acc

        all_bp_ids = extract_all_blueprint_type_ids(type_id)

        # --- Récupération des configurations pour tous les blueprints collectés ---
        if all_bp_ids:
            placeholders = ",".join(["%s"] * len(all_bp_ids))
            params = list(all_bp_ids)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        t.id AS type_id,
                        t.name AS type_name,
                        t.eve_group_id AS group_id,
                        g.name AS group_name,
                        a.activity_id,
                        COALESCE(NULLIF(a.product_eve_type_id, 0), NULLIF(a.eve_type_id, 0)) AS product_type_id,
                        a.quantity,
                        0 AS material_efficiency,
                        0 AS time_efficiency
                    FROM eveuniverse_evetype t
                    JOIN eveuniverse_eveindustryactivityproduct a ON t.id = a.eve_type_id
                    LEFT JOIN eveuniverse_evegroup g ON t.eve_group_id = g.id
                    WHERE t.published = 1
                        AND a.activity_id IN (1, 11)
                        AND t.id IN ({placeholders})
                    ORDER BY g.name, a.quantity DESC
                """,
                    params,
                )
                blueprint_configs = [
                    {
                        "type_id": row[0],
                        "type_name": row[1],
                        "group_id": row[2],
                        "group_name": row[3],
                        "activity_id": row[4],
                        "product_type_id": row[5],
                        "quantity": row[6],
                        "material_efficiency": row[7],
                        "time_efficiency": row[8],
                    }
                    for row in cursor.fetchall()
                ]

                # --- Mise à jour des valeurs ME/TE pour le blueprint principal ---
                for bc in blueprint_configs:
                    if bc["type_id"] == type_id:  # Blueprint principal
                        bc["material_efficiency"] = me
                        bc["time_efficiency"] = te
                        break
        else:
            blueprint_configs = []

        # --- Injection de la structure des matériaux dans chaque blueprint_config ---
        config_map = {bc["type_id"]: bc for bc in blueprint_configs}

        def inject_materials(tree):
            """Injecte récursivement les enfants (matériaux) dans chaque blueprint_config."""
            for node in tree:
                bc = config_map.get(node["type_id"])
                if bc is not None:
                    if "materials" not in bc:
                        bc["materials"] = []
                    existing = {m["type_id"] for m in bc["materials"]}
                    for sub in node.get("sub_materials", []):
                        if sub["type_id"] not in existing:
                            bc["materials"].append({"type_id": sub["type_id"]})
                            existing.add(sub["type_id"])
                        inject_materials([sub])

        inject_materials([{"type_id": type_id, "sub_materials": materials_tree}])

        # --- Calcul du niveau de profondeur pour chaque blueprint ---
        blueprint_configs = collect_blueprints_with_level(blueprint_configs)

        # --- Regroupement par groupe puis par niveau ---
        grouping = {}
        for bc in blueprint_configs:
            # On ne garde que les blueprints utiles (matériaux non vides OU quantité > 0)
            # Et on exclut les blueprints de réaction (activity_id 9, 11) car ils ne peuvent pas être modifiés en ME/TE
            # Et on exclut les blueprints dont les items sont marqués pour achat
            if (
                bc["type_id"] is not None
                and (
                    (bc.get("materials") and len(bc["materials"]) > 0)
                    or bc.get("quantity", 0) > 0
                )
                and bc.get("activity_id")
                not in [9, 11]  # Exclure les Composite Reaction Formulas
                and bc["type_id"] not in blueprint_exclusions
            ):  # Exclure les blueprints marqués pour achat
                grouping.setdefault(
                    bc["group_id"], {"group_name": bc["group_name"], "levels": {}}
                )
                lvl = bc["level"]
                grouping[bc["group_id"]]["levels"].setdefault(lvl, []).append(bc)

        # --- Structuration finale pour le template ---
        blueprint_configs_grouped = []
        for group_id, info in grouping.items():
            levels = []
            for lvl in sorted(info["levels"].keys()):
                # Filtrer les blueprints réellement utiles (ayant des matériaux ou quantité > 0)
                # Et exclure les blueprints de réaction (activity_id 9, 11) car ils ne peuvent pas être modifiés en ME/TE
                # Et exclure les blueprints dont les items sont marqués pour achat
                blueprints_utiles = [
                    bc
                    for bc in info["levels"][lvl]
                    if (
                        (bc.get("materials") and len(bc["materials"]) > 0)
                        or bc.get("quantity", 0) > 0
                    )
                    and bc.get("activity_id")
                    not in [9, 11]  # Exclure les Composite Reaction Formulas
                    and bc["type_id"]
                    not in blueprint_exclusions  # Exclure les blueprints marqués pour achat
                ]
                if blueprints_utiles:
                    levels.append({"level": lvl, "blueprints": blueprints_utiles})
            # Ne garder que les groupes qui ont au moins un blueprint utile dans un niveau
            if levels:
                blueprint_configs_grouped.append(
                    {
                        "group_id": group_id,
                        "group_name": info["group_name"],
                        "levels": levels,
                    }
                )
        if not blueprint_configs_grouped:
            blueprint_configs_grouped = None

        # --- Calcul cumulatif des cycles/produits/surplus pour chaque item craftable ---
        # Standard Library
        from collections import defaultdict

        def collect_craftables(materials, craftables):
            for mat in materials:
                # On ne cumule que si l'item est craftable (produit par un blueprint)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat["type_id"]],
                    )
                    sub_bp_row = cursor.fetchone()
                    if sub_bp_row:
                        # On cumule la quantité demandée
                        craftables[mat["type_id"]]["type_name"] = mat["type_name"]
                        craftables[mat["type_id"]]["total_needed"] += ceil(
                            mat["quantity"]
                        )
                        # On récupère la quantité produite par cycle
                        cursor.execute(
                            """
                            SELECT quantity
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [sub_bp_row[0]],
                        )
                        prod_qty_row = cursor.fetchone()
                        output_qty = prod_qty_row[0] if prod_qty_row else 1
                        craftables[mat["type_id"]]["produced_per_cycle"] = output_qty
                        # On continue dans les sous-matériaux
                        if "sub_materials" in mat:
                            collect_craftables(mat["sub_materials"], craftables)

        craftables = defaultdict(
            lambda: {"type_name": "", "total_needed": 0, "produced_per_cycle": 1}
        )
        collect_craftables(materials_tree, craftables)
        # Calcul cycles, total_produced, surplus
        for v in craftables.values():
            # Standard Library

            v["cycles"] = ceil(v["total_needed"] / v["produced_per_cycle"])
            v["total_produced"] = v["cycles"] * v["produced_per_cycle"]
            v["surplus"] = v["total_produced"] - v["total_needed"]

        # --- Prepare direct materials list (only direct children of the main blueprint) ---
        direct_materials_list = []
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT m.material_eve_type_id, t.name, m.quantity "
                "FROM eveuniverse_eveindustryactivitymaterial m "
                "JOIN eveuniverse_evetype t ON m.material_eve_type_id = t.id "
                "WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)",
                [type_id],
            )
            for row in cursor.fetchall():
                base_qty = row[2] * num_runs
                # Apply ME bonus if applicable and round up to integer
                # Standard Library

                qty = ceil(base_qty * (100 - me) / 100)
                direct_materials_list.append(
                    {
                        "type_id": row[0],
                        "type_name": row[1],
                        "quantity": qty,
                    }
                )

        # --- Prepare materials list (flattened), fallback to direct fetch if empty ---
        materials_list = flatten_materials(materials_tree, buy_decisions)
        if not materials_list:
            # Use direct materials as fallback
            materials_list = direct_materials_list
        # --- Ajout du mapping type_id -> nom de groupe Eve pour l'onglet Financial ---
        # Récupère tous les type_id utilisés dans les matériaux à acheter
        all_type_ids = {mat["type_id"] for mat in materials_list}
        eve_types_query = []
        if EveType is not None:
            eve_types_query = list(
                EveType.objects.filter(id__in=all_type_ids).select_related("eve_group")
            )
        eve_types = eve_types_query
        # On ne garde que les groupes ayant au moins un item dans materials_list
        group_ids_used = set()
        for mat in materials_list:
            eve_type = next((et for et in eve_types if et.id == mat["type_id"]), None)
            if eve_type and eve_type.eve_group:
                group_ids_used.add(eve_type.eve_group.id)
            elif eve_type:
                group_ids_used.add(None)
        market_group_map = {}
        if EveType is not None:
            for eve_type in eve_types:
                group_id = eve_type.eve_group.id if eve_type.eve_group else None
                group_name = eve_type.eve_group.name if eve_type.eve_group else "Other"
                if group_id in group_ids_used:
                    market_group_map[eve_type.id] = {
                        "group_id": group_id,
                        "group_name": group_name,
                    }

        # Nouveau: mapping group_id -> dict avec group_name et liste des items
        materials_by_group = {}
        for mat in materials_list:
            eve_type = next((et for et in eve_types if et.id == mat["type_id"]), None)
            group_id = (
                eve_type.eve_group.id if eve_type and eve_type.eve_group else None
            )
            group_name = (
                eve_type.eve_group.name if eve_type and eve_type.eve_group else "Other"
            )
            if group_id not in materials_by_group:
                materials_by_group[group_id] = {"group_name": group_name, "items": []}
            materials_by_group[group_id]["items"].append(mat)

        def _to_serializable(value):
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, dict):
                return {k: _to_serializable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_to_serializable(item) for item in value]
            return value

        blueprint_payload = {
            "type_id": type_id,
            "bp_type_id": type_id,
            "name": bp_name,
            "num_runs": num_runs,
            "final_product_qty": final_product_qty,
            "product_type_id": product_type_id,
            "me": me,
            "te": te,
            "active_tab": active_tab,
            "materials": _to_serializable(materials_list),
            "direct_materials": _to_serializable(direct_materials_list),
            "materials_tree": _to_serializable(materials_tree),
            "craft_cycles_summary": _to_serializable(dict(craftables)),
            "blueprint_configs_grouped": (
                _to_serializable(blueprint_configs_grouped)
                if blueprint_configs_grouped
                else []
            ),
            "market_group_map": _to_serializable(market_group_map),
            "materials_by_group": _to_serializable(materials_by_group),
            "urls": {
                "save": reverse("indy_hub:save_production_config"),
                "load_list": reverse("indy_hub:production_simulations_list"),
                "load_config": reverse("indy_hub:load_production_config"),
                "fuzzwork_price": reverse("indy_hub:fuzzwork_price"),
            },
        }

        context = {
            "bp_type_id": type_id,
            "bp_name": bp_name,
            "materials": materials_list,
            "direct_materials": direct_materials_list,
            "materials_tree": materials_tree,
            "num_runs": num_runs,
            "product_type_id": product_type_id,
            "final_product_qty": final_product_qty,
            "me": me,
            "te": te,
            "active_tab": active_tab,
            "blueprint_configs_grouped": blueprint_configs_grouped,
            "craft_cycles_summary": dict(craftables),
            "market_group_map": market_group_map,
            "materials_by_group": materials_by_group,
            "blueprint_payload": blueprint_payload,
        }
        return render(request, "indy_hub/Craft_BP.html", context)

    except Exception as e:
        # Gestion d'erreur : on affiche la page avec un message d'erreur et des valeurs par défaut
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM eveuniverse_evetype WHERE id=%s", [type_id]
            )
            row = cursor.fetchone()
            bp_name = row[0] if row else str(type_id)
        messages.error(request, f"Error crafting blueprint: {e}")
        return render(
            request,
            "indy_hub/Craft_BP.html",
            {
                "bp_type_id": type_id,
                "bp_name": bp_name,
                "materials": [],
                "direct_materials": [],
                "materials_tree": [],
                "num_runs": 1,
                "product_type_id": None,
                "me": 0,
                "te": 0,
            },
        )


@indy_hub_access_required
@login_required
def fuzzwork_price(request):
    type_ids = request.GET.get("type_id")
    region_id = request.GET.get("region_id", "10000002")
    if not type_ids:
        return JsonResponse({"error": "type_id required"}, status=400)
    type_id_list = [str(tid) for tid in type_ids.split(",") if tid.strip().isdigit()]
    if not type_id_list:
        return JsonResponse({"error": "No valid type_id"}, status=400)
    try:
        url = f'https://market.fuzzwork.co.uk/aggregates/?region={region_id}&types={"%2C".join(type_id_list)}'
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200 or "application/json" not in resp.headers.get(
            "Content-Type", ""
        ):
            print(
                f"Fuzzwork aggregates API raw response for type_ids={type_id_list}: {resp.text}"
            )
            return JsonResponse(
                {
                    "error": f"Fuzzwork returned status {resp.status_code}",
                    "raw": resp.text,
                },
                status=200,
            )
        data = resp.json()
        result = {}
        for tid in type_id_list:
            try:
                agg = data.get(tid) or data.get(str(tid))
                price = 0.0
                if agg and "sell" in agg and agg["sell"] and "min" in agg["sell"]:
                    price = float(agg["sell"]["min"])
                elif agg and "buy" in agg and agg["buy"] and "max" in agg["buy"]:
                    price = float(agg["buy"]["max"])
                result[tid] = price
            except Exception:
                result[tid] = 0.0
        return JsonResponse(result)
    except Exception as e:
        # Standard Library
        import traceback

        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=200)


@indy_hub_access_required
@login_required
def bp_copy_request_page(request):
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership

    search = request.GET.get("search", "").strip()
    min_me = request.GET.get("min_me", "")
    min_te = request.GET.get("min_te", "")
    page = request.GET.get("page", 1)
    per_page = int(request.GET.get("per_page", 24))
    # Determine viewer affiliations (corporation / alliance)
    viewer_corp_ids: set[int] = set()
    viewer_alliance_ids: set[int] = set()
    viewer_ownerships = CharacterOwnership.objects.filter(
        user=request.user
    ).select_related("character")
    for ownership in viewer_ownerships:
        corp_id = getattr(ownership.character, "corporation_id", None)
        if corp_id:
            viewer_corp_ids.add(corp_id)
        alliance_id = getattr(ownership.character, "alliance_id", None)
        if alliance_id:
            viewer_alliance_ids.add(alliance_id)

    # Fetch users who enabled copy sharing (global settings)
    settings_qs = CharacterSettings.objects.filter(
        character_id=0,
        allow_copy_requests=True,
    ).exclude(copy_sharing_scope=CharacterSettings.SCOPE_NONE)
    settings_list = list(settings_qs)
    owner_user_ids = [setting.user_id for setting in settings_list]

    owner_affiliations: dict[int, dict[str, set[int]]] = {}
    if owner_user_ids:
        owner_ownerships = CharacterOwnership.objects.filter(
            user_id__in=owner_user_ids
        ).select_related("character")
        for ownership in owner_ownerships:
            data = owner_affiliations.setdefault(
                ownership.user_id,
                {"corp_ids": set(), "alliance_ids": set()},
            )
            corp_id = getattr(ownership.character, "corporation_id", None)
            if corp_id:
                data["corp_ids"].add(corp_id)
            alliance_id = getattr(ownership.character, "alliance_id", None)
            if alliance_id:
                data["alliance_ids"].add(alliance_id)

    allowed_user_ids: set[int] = set()
    for setting in settings_list:
        affiliations = owner_affiliations.get(
            setting.user_id, {"corp_ids": set(), "alliance_ids": set()}
        )
        corp_ids = affiliations["corp_ids"]
        alliance_ids = affiliations["alliance_ids"]

        if setting.copy_sharing_scope == CharacterSettings.SCOPE_CORPORATION:
            if viewer_corp_ids & corp_ids:
                allowed_user_ids.add(setting.user_id)
        elif setting.copy_sharing_scope == CharacterSettings.SCOPE_ALLIANCE:
            if (viewer_alliance_ids & alliance_ids) or (viewer_corp_ids & corp_ids):
                allowed_user_ids.add(setting.user_id)

    if not allowed_user_ids:
        qs = Blueprint.objects.none()
    else:
        qs = Blueprint.objects.filter(
            owner_user_id__in=allowed_user_ids,
            bp_type=Blueprint.BPType.ORIGINAL,
        ).order_by("type_name", "material_efficiency", "time_efficiency")
    seen = set()
    bp_list = []
    for bp in qs:
        key = (bp.type_id, bp.material_efficiency, bp.time_efficiency)
        if key in seen:
            continue
        seen.add(key)
        bp_list.append(
            {
                "type_id": bp.type_id,
                "type_name": bp.type_name or str(bp.type_id),
                "icon_url": f"https://images.evetech.net/types/{bp.type_id}/bp?size=32",
                "material_efficiency": bp.material_efficiency,
                "time_efficiency": bp.time_efficiency,
            }
        )
    if search:
        bp_list = [bp for bp in bp_list if search.lower() in bp["type_name"].lower()]
    if min_me.isdigit():
        min_me_val = int(min_me)
        bp_list = [bp for bp in bp_list if bp["material_efficiency"] >= min_me_val]
    if min_te.isdigit():
        min_te_val = int(min_te)
        bp_list = [bp for bp in bp_list if bp["time_efficiency"] >= min_te_val]
    per_page_options = [12, 24, 48, 96]
    me_options = list(range(0, 11))
    te_options = list(range(0, 21))
    paginator = Paginator(bp_list, per_page)
    page_obj = paginator.get_page(page)
    page_range = paginator.get_elided_page_range(
        number=page_obj.number, on_each_side=5, on_ends=1
    )
    if request.method == "POST":
        type_id = int(request.POST.get("type_id", 0))
        me = int(request.POST.get("material_efficiency", 0))
        te = int(request.POST.get("time_efficiency", 0))
        runs = int(request.POST.get("runs_requested", 1))
        copies = int(request.POST.get("copies_requested", 1))
        BlueprintCopyRequest.objects.create(
            type_id=type_id,
            material_efficiency=me,
            time_efficiency=te,
            requested_by=request.user,
            runs_requested=runs,
            copies_requested=copies,
        )
        # Django
        from django.contrib.auth.models import User

        owner_ids = (
            Blueprint.objects.filter(
                type_id=type_id,
                bp_type=Blueprint.BPType.ORIGINAL,
            )
            .values_list("owner_user", flat=True)
            .distinct()
        )
        for owner in User.objects.filter(id__in=owner_ids):
            notify_user(
                owner,
                "New blueprint copy request",
                f"{request.user.username} requested a copy of {get_type_name(type_id)} (ME{me}, TE{te})",
                "info",
            )
        messages.success(request, "Copy request sent.")
        return redirect("indy_hub:bp_copy_request_page")
    return render(
        request,
        "indy_hub/bp_copy_request_page.html",
        {
            "page_obj": page_obj,
            "search": search,
            "min_me": min_me,
            "min_te": min_te,
            "per_page": per_page,
            "per_page_options": per_page_options,
            "me_options": me_options,
            "te_options": te_options,
            "page_range": page_range,
            "requests": [],
        },
    )


@indy_hub_access_required
@login_required
def bp_copy_fulfill_requests(request):
    """List requests for blueprints the user owns and allows copy requests for."""
    from ..models import CharacterSettings

    setting = CharacterSettings.objects.filter(
        user=request.user,
        character_id=0,  # Global settings only
        allow_copy_requests=True,
    ).first()
    if not setting:
        return render(
            request, "indy_hub/bp_copy_fulfill_requests.html", {"requests": []}
        )
    my_bps_qs = Blueprint.objects.filter(
        owner_user=request.user,
        bp_type=Blueprint.BPType.ORIGINAL,
    )
    my_bps = list(my_bps_qs)

    bp_index = defaultdict(list)
    bp_item_map = {}

    for bp in my_bps:
        key = (bp.type_id, bp.material_efficiency, bp.time_efficiency)
        bp_index[key].append(bp)
        if bp.item_id is not None:
            bp_item_map[bp.item_id] = key

    status_meta = {
        "awaiting_response": {
            "label": _("Awaiting response"),
            "badge": "bg-warning text-dark",
            "hint": _(
                "No offer sent yet. Accept, reject, or propose conditions to help your corpmate."
            ),
        },
        "waiting_on_buyer": {
            "label": _("Waiting on buyer"),
            "badge": "bg-info text-white",
            "hint": _("You've sent a conditional offer. Awaiting buyer confirmation."),
        },
        "ready_to_deliver": {
            "label": _("Ready to deliver"),
            "badge": "bg-success text-white",
            "hint": _(
                "Buyer accepted your offer. Deliver the copies and mark the request as complete."
            ),
        },
        "offer_rejected": {
            "label": _("Offer rejected"),
            "badge": "bg-danger text-white",
            "hint": _(
                "Your previous offer was declined. Consider sending an updated proposal."
            ),
        },
        "self_request": {
            "label": _("Your tracked request"),
            "badge": "bg-secondary text-white",
            "hint": _(
                "You posted this ask. Another builder will pick it up; no action needed from you."
            ),
        },
    }

    metrics = {
        "total": 0,
        "awaiting_response": 0,
        "waiting_on_buyer": 0,
        "ready_to_deliver": 0,
        "offer_rejected": 0,
    }

    if not my_bps:
        return render(
            request,
            "indy_hub/bp_copy_fulfill_requests.html",
            {"requests": [], "metrics": metrics},
        )

    q = Q()
    has_filters = False
    for bp in my_bps:
        has_filters = True
        q |= Q(
            type_id=bp.type_id,
            material_efficiency=bp.material_efficiency,
            time_efficiency=bp.time_efficiency,
        )

    if not has_filters:
        return render(
            request,
            "indy_hub/bp_copy_fulfill_requests.html",
            {"requests": [], "metrics": metrics},
        )

    def _init_occupancy():
        return {"count": 0, "soonest_end": None}

    occupancy_map = defaultdict(_init_occupancy)

    def _update_soonest(info, end_date):
        if end_date and (info["soonest_end"] is None or end_date < info["soonest_end"]):
            info["soonest_end"] = end_date

    blocking_activities = [1, 3, 4, 5, 8, 9]
    active_jobs = IndustryJob.objects.filter(
        owner_user=request.user,
        status="active",
        activity_id__in=blocking_activities,
    ).only("blueprint_id", "blueprint_type_id", "end_date")

    for job in active_jobs:
        matched_key = bp_item_map.get(job.blueprint_id)
        if matched_key is not None:
            info = occupancy_map[matched_key]
            info["count"] += 1
            _update_soonest(info, job.end_date)

    offer_status_labels = {
        "accepted": _("Accepted"),
        "conditional": _("Conditional"),
        "rejected": _("Rejected"),
    }

    qset = (
        BlueprintCopyRequest.objects.filter(q)
        .filter(
            Q(fulfilled=False)
            | Q(fulfilled=True, delivered=False, offers__owner=request.user)
        )
        .select_related("requested_by")
        .prefetch_related("offers__owner")
        .order_by("-created_at")
        .distinct()
    )

    requests_to_fulfill = []
    for req in qset:
        offers = list(req.offers.all())
        my_offer = next(
            (offer for offer in offers if offer.owner_id == request.user.id), None
        )

        is_self_request = req.requested_by_id == request.user.id

        if req.fulfilled and (req.delivered or not my_offer):
            # Already delivered or fulfilled by someone else
            continue

        status_key = "awaiting_response"
        can_mark_delivered = False

        key = (req.type_id, req.material_efficiency, req.time_efficiency)
        matching_blueprints = bp_index.get(key, [])
        owned_blueprints = len(matching_blueprints)
        direct_info = occupancy_map.get(key)
        direct_count = direct_info["count"] if direct_info else 0
        total_active_jobs = min(owned_blueprints, direct_count)
        available_blueprints = max(owned_blueprints - total_active_jobs, 0)
        busy_until = direct_info["soonest_end"] if direct_info else None
        busy_overdue = bool(busy_until and busy_until < timezone.now())
        all_copies_busy = (
            owned_blueprints > 0 and available_blueprints == 0 and total_active_jobs > 0
        )

        if is_self_request:
            status_key = "self_request"
        elif req.fulfilled and not req.delivered:
            status_key = "ready_to_deliver"
            can_mark_delivered = True
        elif my_offer:
            if my_offer.status == "conditional":
                if my_offer.accepted_by_buyer:
                    status_key = "ready_to_deliver"
                    can_mark_delivered = True
                else:
                    status_key = "waiting_on_buyer"
            elif my_offer.status == "rejected":
                status_key = "offer_rejected"
            elif my_offer.status == "accepted":
                status_key = "ready_to_deliver"
                can_mark_delivered = True
        else:
            status_key = "awaiting_response"

        metrics["total"] += 1
        metrics_key = {
            "awaiting_response": "awaiting_response",
            "waiting_on_buyer": "waiting_on_buyer",
            "ready_to_deliver": "ready_to_deliver",
            "offer_rejected": "offer_rejected",
        }.get(status_key)
        if metrics_key and not (metrics_key == "awaiting_response" and is_self_request):
            metrics[metrics_key] += 1

        status_info = status_meta[status_key]

        requests_to_fulfill.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": get_type_name(req.type_id),
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=64",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "runs_requested": req.runs_requested,
                "copies_requested": getattr(req, "copies_requested", 1),
                "created_at": req.created_at,
                "requester": req.requested_by.username,
                "is_self_request": is_self_request,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_info["hint"],
                "my_offer_status": getattr(my_offer, "status", None),
                "my_offer_status_label": offer_status_labels.get(
                    getattr(my_offer, "status", None), ""
                ),
                "my_offer_message": getattr(my_offer, "message", ""),
                "my_offer_accepted_by_buyer": getattr(
                    my_offer, "accepted_by_buyer", False
                ),
                "show_offer_actions": status_key
                in {"awaiting_response", "offer_rejected"},
                "conditional_collapse_id": f"cond-{req.id}",
                "can_mark_delivered": can_mark_delivered
                and req.requested_by_id != request.user.id,
                "owned_blueprints": owned_blueprints,
                "available_blueprints": available_blueprints,
                "active_copy_jobs": total_active_jobs,
                "all_copies_busy": all_copies_busy,
                "busy_until": busy_until,
                "busy_overdue": busy_overdue,
            }
        )

    return render(
        request,
        "indy_hub/bp_copy_fulfill_requests.html",
        {"requests": requests_to_fulfill, "metrics": metrics},
    )


@indy_hub_access_required
@login_required
def bp_offer_copy_request(request, request_id):
    """Handle offering to fulfill a blueprint copy request."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    action = request.POST.get("action")
    message = request.POST.get("message", "").strip()
    offer, created = BlueprintCopyOffer.objects.get_or_create(
        request=req, owner=request.user
    )
    if action == "accept":
        offer.status = "accepted"
        offer.message = ""
        offer.accepted_by_buyer = False
        offer.save()
        # Notify requester: accepted (free)
        notify_user(
            req.requested_by,
            "Blueprint Copy Request Accepted",
            f"{request.user.username} accepted your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) for free.",
            "success",
        )
        # Mark request as fulfilled, remove all other offers
        req.fulfilled = True
        req.fulfilled_at = timezone.now()
        req.save()
        BlueprintCopyOffer.objects.filter(request=req).exclude(
            owner=request.user
        ).delete()
        messages.success(request, "Request accepted and requester notified.")
    elif action == "conditional":
        offer.status = "conditional"
        offer.message = message
        offer.accepted_by_buyer = False
        offer.save()
        # Notify requester: conditional offer
        notify_user(
            req.requested_by,
            "Blueprint Copy Request - Conditional Offer",
            f"{request.user.username} proposes: {message}",
            "info",
        )
        messages.success(request, "Conditional offer sent.")
    elif action == "reject":
        offer.status = "rejected"
        offer.message = message
        offer.accepted_by_buyer = False
        offer.save()
        messages.success(request, "Offer rejected.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@login_required
def bp_buyer_accept_offer(request, offer_id):
    """Allow buyer to accept a conditional offer."""
    offer = get_object_or_404(
        BlueprintCopyOffer, id=offer_id, status="conditional", accepted_by_buyer=False
    )
    offer.accepted_by_buyer = True
    offer.accepted_at = timezone.now()
    offer.save()
    # Mark request as fulfilled, remove other offers
    req = offer.request
    req.fulfilled = True
    req.fulfilled_at = timezone.now()
    req.save()
    BlueprintCopyOffer.objects.filter(request=req).exclude(id=offer.id).delete()
    # Notify seller
    notify_user(
        offer.owner,
        "Blueprint Copy Request - Buyer Accepted",
        f"{req.requested_by.username} accepted your offer for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}).",
        "success",
    )
    messages.success(request, "Offer accepted. Seller notified.")
    return redirect("indy_hub:bp_copy_request_page")


@indy_hub_access_required
@login_required
def bp_accept_copy_request(request, request_id):
    """Accept a blueprint copy request and notify requester."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    req.fulfilled = True
    req.fulfilled_at = timezone.now()
    req.save()
    # Notify requester
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Accepted",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) has been accepted.",
        "success",
    )
    messages.success(request, "Copy request accepted.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@login_required
def bp_cond_copy_request(request, request_id):
    """Send conditional acceptance message for a blueprint copy request."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    message = request.POST.get("message", "").strip()
    if message:
        notify_user(
            req.requested_by, "Blueprint Copy Request Condition", message, "info"
        )
        messages.success(request, "Condition message sent to requester.")
    else:
        messages.error(request, "No message provided for condition.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@login_required
def bp_reject_copy_request(request, request_id):
    """Reject a blueprint copy request and notify requester."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Rejected",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) was rejected.",
        "warning",
    )
    req.delete()
    messages.success(request, "Copy request rejected.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@login_required
def bp_cancel_copy_request(request, request_id):
    """Allow user to cancel their own unfulfilled copy request."""
    req = get_object_or_404(
        BlueprintCopyRequest, id=request_id, requested_by=request.user, fulfilled=False
    )
    offers = req.offers.all()
    for offer in offers:
        notify_user(
            offer.owner,
            "Blueprint Copy Request Cancelled",
            f"{request.user.username} cancelled their copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}).",
            "warning",
        )
    offers.delete()
    req.delete()
    messages.success(request, "Copy request cancelled.")
    return redirect("indy_hub:bp_copy_request_page")


@indy_hub_access_required
@login_required
def bp_mark_copy_delivered(request, request_id):
    """Mark a fulfilled blueprint copy request as delivered (provider action)."""
    req = get_object_or_404(
        BlueprintCopyRequest, id=request_id, fulfilled=True, delivered=False
    )
    req.delivered = True
    req.delivered_at = timezone.now()
    req.save()
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Delivered",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) has been marked as delivered.",
        "success",
    )
    messages.success(request, "Request marked as delivered.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@login_required
def bp_copy_my_requests(request):
    """List copy requests made by the current user."""
    qs = (
        BlueprintCopyRequest.objects.filter(requested_by=request.user)
        .select_related("requested_by")
        .prefetch_related("offers__owner")
        .order_by("-created_at")
    )

    status_meta = {
        "open": {
            "label": _("Awaiting provider"),
            "badge": "bg-warning text-dark",
            "hint": _("No builder has accepted yet. Keep an eye out for new offers."),
        },
        "action_required": {
            "label": _("Your action needed"),
            "badge": "bg-info text-white",
            "hint": _(
                "Review conditional offers and accept the one that suits you best."
            ),
        },
        "awaiting_delivery": {
            "label": _("In progress"),
            "badge": "bg-success text-white",
            "hint": _(
                "A builder accepted. Coordinate delivery and watch for the completion notice."
            ),
        },
        "delivered": {
            "label": _("Delivered"),
            "badge": "bg-secondary text-white",
            "hint": _("Blueprint copies have been delivered. Enjoy!"),
        },
    }

    metrics = {
        "total": 0,
        "open": 0,
        "action_required": 0,
        "awaiting_delivery": 0,
        "delivered": 0,
    }

    my_requests = []
    for req in qs:
        offers = list(req.offers.all())
        accepted_offer_obj = next(
            (offer for offer in offers if offer.status == "accepted"), None
        )
        cond_accepted_obj = next(
            (
                offer
                for offer in offers
                if offer.status == "conditional" and offer.accepted_by_buyer
            ),
            None,
        )
        cond_offers = [
            offer
            for offer in offers
            if offer.status == "conditional" and not offer.accepted_by_buyer
        ]

        status_key = "open"
        if req.delivered:
            status_key = "delivered"
        elif req.fulfilled:
            status_key = "awaiting_delivery"
        elif cond_offers:
            status_key = "action_required"

        metrics["total"] += 1
        metrics_key = {
            "open": "open",
            "action_required": "action_required",
            "awaiting_delivery": "awaiting_delivery",
            "delivered": "delivered",
        }.get(status_key)
        if metrics_key:
            metrics[metrics_key] += 1

        status_info = status_meta[status_key]

        accepted_offer = (
            {
                "owner_username": accepted_offer_obj.owner.username,
                "message": accepted_offer_obj.message,
            }
            if accepted_offer_obj
            else None
        )
        cond_accepted = (
            {
                "owner_username": cond_accepted_obj.owner.username,
                "message": cond_accepted_obj.message,
            }
            if cond_accepted_obj
            else None
        )
        cond_offer_data = [
            {
                "id": offer.id,
                "owner_username": offer.owner.username,
                "message": offer.message,
            }
            for offer in cond_offers
        ]

        my_requests.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": get_type_name(req.type_id),
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=64",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "copies_requested": req.copies_requested,
                "runs_requested": req.runs_requested,
                "accepted_offer": accepted_offer,
                "cond_accepted": cond_accepted,
                "cond_offers": cond_offer_data,
                "delivered": req.delivered,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_info["hint"],
                "created_at": req.created_at,
                "can_cancel": not req.fulfilled,
            }
        )

    return render(
        request,
        "indy_hub/bp_copy_my_requests.html",
        {"my_requests": my_requests, "metrics": metrics},
    )


@indy_hub_access_required
@login_required
def production_simulations_list(request):
    """
    Affiche la liste des simulations de production sauvegardées par l'utilisateur.
    Peut retourner du JSON si api=1 est passé en paramètre.
    """
    simulations = (
        ProductionSimulation.objects.filter(user=request.user)
        .order_by("-updated_at")
        .prefetch_related("production_configs")
    )

    # Si demande API, retourner du JSON
    if request.GET.get("api") == "1":
        simulations_data = []
        for sim in simulations:
            simulations_data.append(
                {
                    "id": sim.id,
                    "blueprint_type_id": sim.blueprint_type_id,
                    "blueprint_name": sim.blueprint_name,
                    "runs": sim.runs,
                    "simulation_name": sim.simulation_name,
                    "display_name": sim.display_name,
                    "total_items": sim.total_items,
                    "total_buy_items": sim.total_buy_items,
                    "total_prod_items": sim.total_prod_items,
                    "estimated_cost": float(sim.estimated_cost),
                    "estimated_revenue": float(sim.estimated_revenue),
                    "estimated_profit": float(sim.estimated_profit),
                    "active_tab": sim.active_tab,
                    "created_at": sim.created_at.isoformat(),
                    "updated_at": sim.updated_at.strftime("%Y-%m-%d %H:%M"),
                }
            )

        return JsonResponse(
            {
                "success": True,
                "simulations": simulations_data,
                "total_simulations": simulations.count(),
            }
        )

    # Préparer les statistiques agrégées pour l'affichage HTML
    total_simulations = simulations.count()
    stats_raw = simulations.aggregate(
        total_runs=Sum("runs"),
        total_items=Sum("total_items"),
        total_buy_items=Sum("total_buy_items"),
        total_prod_items=Sum("total_prod_items"),
        total_cost=Sum("estimated_cost"),
        total_revenue=Sum("estimated_revenue"),
        total_profit=Sum("estimated_profit"),
    )
    stats = {key: (stats_raw.get(key) or 0) for key in stats_raw}
    stats["average_profit"] = (
        stats["total_profit"] / total_simulations
        if total_simulations and stats["total_profit"]
        else 0
    )
    latest_simulation = simulations.first()
    stats["latest_update"] = latest_simulation.updated_at if latest_simulation else None

    # Sinon, affichage HTML normal
    # Pagination
    paginator = Paginator(simulations, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # --- Ajout du mapping type_id -> nom de groupe Eve pour Craft_BP.html ---
    # Récupère tous les type_id utilisés dans les simulations de l'utilisateur
    type_ids = set()
    for sim in simulations:
        configs = sim.production_configs.all()
        for config in configs:
            type_ids.add(config.item_type_id)
    # Récupère le nom du groupe Eve pour chaque type_id
    market_group_map = {}
    if EveType is not None and type_ids:
        eve_types = EveType.objects.filter(id__in=type_ids).select_related("eve_group")
        for eve_type in eve_types:
            market_group_map[eve_type.id] = (
                eve_type.eve_group.name if eve_type.eve_group else "Other"
            )
    context = {
        "simulations": page_obj,
        "total_simulations": total_simulations,
        "market_group_map": json.dumps(market_group_map),
        "stats": stats,
    }
    return render(request, "indy_hub/production_simulations_list.html", context)


@indy_hub_access_required
@login_required
def delete_production_simulation(request, simulation_id):
    """
    Supprime une simulation de production et ses configurations associées.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        blueprint_type_id = simulation.blueprint_type_id
        runs = simulation.runs
        simulation_name = simulation.display_name

        # Supprimer les configurations associées (nouvelle relation directe)
        related_configs = simulation.production_configs.all()
        if related_configs.exists():
            related_configs.delete()
        else:
            # Fallback legacy: nettoyer d'anciennes lignes sans FK renseignée
            ProductionConfig.objects.filter(
                user=request.user,
                blueprint_type_id=blueprint_type_id,
                runs=runs,
                simulation__isnull=True,
            ).delete()

        # Supprimer la simulation
        simulation.delete()

        messages.success(
            request, f'Simulation "{simulation_name}" supprimée avec succès.'
        )
        return redirect("indy_hub:production_simulations_list")

    context = {
        "simulation": simulation,
    }

    return render(request, "indy_hub/confirm_delete_simulation.html", context)


@indy_hub_access_required
@login_required
def edit_simulation_name(request, simulation_id):
    """
    Permet de modifier le nom personnalisé d'une simulation.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        new_name = request.POST.get("simulation_name", "").strip()
        simulation.simulation_name = new_name
        simulation.save()

        messages.success(request, "Nom de la simulation mis à jour avec succès.")
        return redirect("indy_hub:production_simulations_list")

    context = {
        "simulation": simulation,
    }

    return render(request, "indy_hub/edit_simulation_name.html", context)
