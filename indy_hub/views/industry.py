# Industry-related views
# Standard Library
import logging

# Third Party
import requests

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import indy_hub_access_required
from ..models import (
    Blueprint,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    BlueprintCopyShareSetting,
    CharacterUpdateTracker,
    IndustryJob,
    get_character_name,
    get_type_name,
)
from ..notifications import notify_user
from ..tasks.industry import update_blueprints_for_user, update_industry_jobs_for_user

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
            CharacterUpdateTracker.objects.filter(user=request.user).update(
                last_refresh_request=timezone.now()
            )
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
    per_page = int(request.GET.get("per_page", 50))

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
            blueprints_qs = blueprints_qs.filter(quantity=-1)
        elif type_filter == "copy":
            blueprints_qs = blueprints_qs.filter(quantity=-2)
        elif type_filter == "stack":
            blueprints_qs = blueprints_qs.filter(quantity__gt=0)
        if character_filter:
            blueprints_qs = blueprints_qs.filter(character_id=character_filter)
        blueprints_qs = blueprints_qs.order_by("type_name")
        # Group identical items by type, ME, TE; count originals and stacks
        bp_items = []
        stack_map = {}
        for bp in blueprints_qs:
            # Reaction blueprints (activity_id 9,11) are always originals
            if activity_id == "9,11":
                category = "original"
            else:
                # separate originals, copies, and stacks into distinct groups
                if bp.is_original:
                    category = "original"
                elif bp.is_copy:
                    category = "copy"
                else:
                    category = "stack"
            key = (bp.type_id, bp.material_efficiency, bp.time_efficiency, category)
            if key not in stack_map:
                # initialize counters for this combination
                bp.orig_quantity = 0
                bp.copy_quantity = 0
                bp.stack_quantity = 0
                stack_map[key] = bp
                bp_items.append(bp)
            agg = stack_map[key]
            if activity_id == "9,11":
                # count stacks by quantity and other entries as originals for reactions
                if bp.is_stack:
                    agg.stack_quantity += bp.quantity
                else:
                    agg.orig_quantity += 1
            else:
                if bp.is_original:
                    # count each original entry
                    agg.orig_quantity += 1
                elif bp.is_copy:
                    # count each blueprint copy entry
                    agg.copy_quantity += 1
                elif bp.is_stack:
                    # sum all stack quantities
                    agg.stack_quantity += bp.quantity
        # Compute total quantity (originals + stacks) for each grouped blueprint
        for bp in bp_items:
            # total identical blueprints: originals + copies + stack quantities
            bp.total_quantity = bp.orig_quantity + bp.copy_quantity + bp.stack_quantity
        # Compute precise human-readable location path for each blueprint
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveStation, EveStructure
        except ImportError:
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
            # flag reaction blueprints for template
            bp.is_reaction = activity_id == "9,11"
        # Django pagination on grouped items
        # Django
        from django.core.paginator import Paginator

        paginator = Paginator(bp_items, per_page)
        blueprints_page = paginator.get_page(page)
        total_blueprints = blueprints_qs.count()
        originals_count = blueprints_qs.filter(quantity=-1).count()
        copies_count = blueprints_qs.filter(quantity=-2).count()
        stacks_count = 0
        try:
            if total_blueprints == 0:
                stacks_count = 0
            else:
                stacks_count = blueprints_qs.filter(quantity__gt=0).count()
        except Exception:
            stacks_count = 0
        character_ids = (
            Blueprint.objects.filter(owner_user=request.user)
            .values_list("character_id", flat=True)
            .distinct()
        )
        character_map = {cid: get_character_name(cid) for cid in character_ids}
        update_status = CharacterUpdateTracker.objects.filter(user=request.user).first()

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
                "stack_blueprints": stacks_count,
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
            "update_status": update_status,
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
    per_page = int(request.GET.get("per_page", 50))
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
            CharacterUpdateTracker.objects.filter(user=request.user).update(
                last_refresh_request=timezone.now()
            )
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
        # Django
        now = timezone.now()
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
        update_status = CharacterUpdateTracker.objects.filter(user=request.user).first()
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
            "update_status": update_status,
            "character_map": character_map,
            "jobs_page": jobs_page,
        }
        # progress_percent and display_eta now available via model properties in template
        return render(request, "indy_hub/Personnal_Job_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying industry jobs: {e}")
        messages.error(request, f"Error displaying industry jobs: {e}")
        return redirect("indy_hub:index")


@indy_hub_access_required
@login_required
def craft_bp(request, type_id):
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
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM eveuniverse_evetype WHERE id=%s", [type_id]
            )
            row = cursor.fetchone()
            bp_name = row[0] if row else str(type_id)
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

        def get_materials_tree(bp_id, runs, depth=0, max_depth=10, seen=None):
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
                    qty = base_qty * (100 - me) / 100
                    mats.append(
                        {"type_id": row[0], "type_name": row[1], "quantity": qty}
                    )
            result = []
            for mat in mats:
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
                        from math import ceil

                        runs_for_sub = ceil(mat["quantity"] / output_qty)
                        mat["sub_materials"] = get_materials_tree(
                            sub_bp_id, runs_for_sub, depth + 1, max_depth, seen.copy()
                        )
                    else:
                        mat["sub_materials"] = []
                result.append(mat)
            return result

        materials_tree = get_materials_tree(type_id, num_runs)

        def flatten_materials(materials):
            return [
                {
                    "type_id": m["type_id"],
                    "type_name": m["type_name"],
                    "quantity": m["quantity"],
                }
                for m in materials
            ]

        return render(
            request,
            "indy_hub/Craft_BP.html",
            {
                "bp_type_id": type_id,
                "bp_name": bp_name,
                "materials": flatten_materials(materials_tree),
                "materials_tree": materials_tree,
                "num_runs": num_runs,
                "product_type_id": product_type_id,
                "final_product_qty": final_product_qty,
                "me": me,
                "te": te,
            },
        )
    except Exception as e:
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
    search = request.GET.get("search", "").strip()
    min_me = request.GET.get("min_me", "")
    min_te = request.GET.get("min_te", "")
    page = request.GET.get("page", 1)
    per_page = int(request.GET.get("per_page", 50))
    # Fetch users who enabled copy sharing, excluding current user
    allowed_users = (
        BlueprintCopyShareSetting.objects.filter(allow_copy_requests=True)
        .exclude(user=request.user)
        .values_list("user", flat=True)
    )
    qs = Blueprint.objects.filter(owner_user__in=allowed_users, quantity=-1).order_by(
        "type_name", "material_efficiency", "time_efficiency"
    )
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
    per_page_options = [10, 25, 50, 100]
    me_options = list(range(0, 11))
    te_options = list(range(0, 21))
    paginator = Paginator(bp_list, per_page)
    page_obj = paginator.get_page(page)
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
            Blueprint.objects.filter(type_id=type_id, quantity=-1)
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
            "requests": [],
        },
    )


@indy_hub_access_required
@login_required
def bp_copy_fulfill_requests(request):
    """List requests for blueprints the user owns and allows copy requests for."""
    from ..models import BlueprintCopyShareSetting

    setting = BlueprintCopyShareSetting.objects.filter(
        user=request.user, allow_copy_requests=True
    ).first()
    if not setting:
        return render(
            request, "indy_hub/bp_copy_fulfill_requests.html", {"requests": []}
        )
    my_bps = Blueprint.objects.filter(owner_user=request.user, quantity=-1)
    q = Q()
    for bp in my_bps:
        q |= Q(
            type_id=bp.type_id,
            material_efficiency=bp.material_efficiency,
            time_efficiency=bp.time_efficiency,
            fulfilled=False,
        )
    # Only show requests that are accepted (fulfilled=True) but not yet delivered
    qset = BlueprintCopyRequest.objects.filter(
        q, fulfilled=True, delivered=False
    ).order_by("-created_at")
    requests_to_fulfill = []
    for req in qset:
        my_offer = BlueprintCopyOffer.objects.filter(
            request=req, owner=request.user
        ).first()
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
                "my_offer": my_offer,
                "delivered": req.delivered,
            }
        )
    return render(
        request,
        "indy_hub/bp_copy_fulfill_requests.html",
        {"requests": requests_to_fulfill},
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
    qs = BlueprintCopyRequest.objects.filter(requested_by=request.user).order_by(
        "-created_at"
    )
    my_requests = []
    for req in qs:
        offers = req.offers.all()
        accepted_offer = offers.filter(status="accepted").first()
        cond_accepted = offers.filter(
            status="conditional", accepted_by_buyer=True
        ).first()
        cond_offers = offers.filter(status="conditional", accepted_by_buyer=False)
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
                "cond_offers": cond_offers,
                "delivered": req.delivered,
            }
        )
    return render(
        request, "indy_hub/bp_copy_my_requests.html", {"my_requests": my_requests}
    )
