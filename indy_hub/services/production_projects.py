"""Helpers for multi-product production project imports and aggregation."""

from __future__ import annotations

# Standard Library
import re
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from datetime import timedelta
from math import ceil
from time import perf_counter

# Django
from django.db import connection

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

from ..models import (
    PROJECT_REF_BASE36_ALPHABET,
    PROJECT_REF_LENGTH,
    Blueprint,
    ProductionProject,
    ProductionProjectItem,
    ProductionSimulation,
)
from ..utils.eve import get_blueprint_product_type_id, get_type_name
from .craft_materials import (
    compute_job_material_quantity,
    is_base_item_material_efficiency_exempt,
)
from .craft_structures import build_craft_structure_planner
from .craft_times import build_craft_time_map
from .industry_skills import build_craft_character_advisor

logger = get_extension_logger(__name__)

EFT_HEADER_RE = re.compile(r"^\[(?P<hull>[^,\]]+?)\s*,\s*(?P<fit_name>[^\]]*?)\s*\]$")
QUANTITY_SUFFIX_RE = re.compile(r"^(?P<name>.+?)\s+x(?P<quantity>\d+)$", re.IGNORECASE)
QUANTITY_PREFIX_RE = re.compile(
    r"^(?P<quantity>\d+)\s*x?\s+(?P<name>.+)$", re.IGNORECASE
)
STRATEGIC_CRUISER_SUBSYSTEM_NAME_RE = re.compile(
    r"\b(?:legion|loki|proteus|tengu)\s+(?:defensive|offensive|core|propulsion)\b",
    re.IGNORECASE,
)
EFT_CATEGORY_SPECS: list[dict[str, object]] = [
    {"key": "low_slots", "label": "Low slots", "order": 10},
    {"key": "mid_slots", "label": "Mid slots", "order": 20},
    {"key": "high_slots", "label": "High slots", "order": 30},
    {"key": "rig_slots", "label": "Rig slots", "order": 40},
    {"key": "subsystems", "label": "Subsystems", "order": 50},
    {"key": "drone_bay", "label": "Drone bay", "order": 60},
    {"key": "cargo", "label": "Cargo", "order": 70},
]

MANUAL_CATEGORY = {"key": "manual", "label": "Manual list", "order": 90}
HULL_CATEGORY = {"key": "hull", "label": "Hull", "order": 0}
EFT_CATEGORY_SPEC_BY_KEY = {
    str(category["key"]): category for category in EFT_CATEGORY_SPECS
}
DRONE_GROUP_KEYWORDS = ("drone", "fighter", "fighter bomber")
SUBSYSTEM_GROUP_KEYWORDS = ("subsystem", "strategic cruiser")


def detect_project_import_kind(raw_text: str) -> str:
    """Return the most likely import kind for raw project input."""

    first_line = next(
        (line.strip() for line in raw_text.splitlines() if line.strip()), ""
    )
    if first_line and EFT_HEADER_RE.match(first_line):
        return "eft"
    return "manual"


def parse_project_import_text(
    raw_text: str,
    *,
    preferred_kind: str | None = None,
) -> dict[str, object]:
    """Parse EFT or manual input into normalized import entries."""

    normalized_text = (raw_text or "").replace("\r\n", "\n")
    kind = preferred_kind or detect_project_import_kind(normalized_text)

    if kind == "eft":
        return _parse_eft_project_import(normalized_text)
    return _parse_manual_project_import(normalized_text)


def normalize_production_project_ref(project_ref: str) -> str:
    """Validate and normalize a fixed-width base36 project token."""

    token = str(project_ref or "").strip().lower()
    if len(token) != PROJECT_REF_LENGTH:
        raise ValueError("Invalid production project token length.")
    if any(char not in PROJECT_REF_BASE36_ALPHABET for char in token):
        raise ValueError("Invalid production project token alphabet.")
    return token


def aggregate_project_import_entries(
    entries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Aggregate duplicate import entries into one line with summed quantities."""

    aggregated: OrderedDict[str, dict[str, object]] = OrderedDict()
    for entry in entries:
        type_name = str(entry.get("type_name") or "").strip()
        if not type_name:
            continue

        quantity = max(1, int(entry.get("quantity") or 1))
        aggregate_key = str(entry.get("type_id") or "").strip() or type_name.casefold()
        category_key = str(entry.get("category_key") or "")
        category_label = str(entry.get("category_label") or "")
        category_order = _coerce_category_order(entry.get("category_order"))

        if aggregate_key not in aggregated:
            aggregated[aggregate_key] = {
                "type_id": entry.get("type_id"),
                "type_name": type_name,
                "quantity": quantity,
                "category_key": category_key,
                "category_label": category_label,
                "category_order": category_order,
                "categories": (
                    [
                        {
                            "key": category_key,
                            "label": category_label,
                            "order": category_order,
                        }
                    ]
                    if category_key or category_label
                    else []
                ),
                "source_lines": [str(entry.get("source_line") or type_name)],
            }
            continue

        current = aggregated[aggregate_key]
        current["quantity"] = int(current.get("quantity") or 0) + quantity
        current["source_lines"].append(str(entry.get("source_line") or type_name))

        seen_category_keys = {
            str(cat.get("key") or "") for cat in current.get("categories", [])
        }
        if category_key and category_key not in seen_category_keys:
            current.setdefault("categories", []).append(
                {
                    "key": category_key,
                    "label": category_label,
                    "order": category_order,
                }
            )

        current_order = _coerce_category_order(current.get("category_order"))
        if category_order < current_order:
            current["category_order"] = category_order
            current["category_key"] = category_key
            current["category_label"] = category_label

    return list(aggregated.values())


def resolve_project_import_entries(
    entries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Resolve imported names against SDE and annotate craftability."""

    if not entries:
        return []

    names = []
    seen_names: set[str] = set()
    for entry in entries:
        normalized_name = str(entry.get("type_name") or "").strip()
        if not normalized_name:
            continue
        lowered = normalized_name.casefold()
        if lowered in seen_names:
            continue
        seen_names.add(lowered)
        names.append(normalized_name)

    resolved_type_map = _resolve_item_types_by_name(names)
    blueprint_map = _resolve_blueprints_for_products(
        [
            info["type_id"]
            for info in resolved_type_map.values()
            if info.get("type_id") is not None
        ]
    )

    resolved_entries: list[dict[str, object]] = []
    for entry in entries:
        type_name = str(entry.get("type_name") or "").strip()
        resolved_info = resolved_type_map.get(type_name.casefold())
        enriched = dict(entry)

        if not resolved_info:
            enriched.update(
                {
                    "resolved": False,
                    "type_id": None,
                    "is_craftable": False,
                    "blueprint_type_id": None,
                    "not_craftable_reason": "unknown_item",
                }
            )
            resolved_entries.append(enriched)
            continue

        type_id = int(resolved_info["type_id"])
        blueprint_type_id = blueprint_map.get(type_id)
        enriched.update(
            {
                "resolved": True,
                "type_id": type_id,
                "type_name": resolved_info.get("type_name") or type_name,
                "group_name": resolved_info.get("group_name") or "",
                "is_craftable": blueprint_type_id is not None,
                "blueprint_type_id": blueprint_type_id,
                "not_craftable_reason": None if blueprint_type_id else "no_blueprint",
            }
        )
        category_override = _normalize_resolved_eft_category(
            enriched,
            str(resolved_info.get("group_name") or ""),
        )
        if category_override:
            enriched.update(
                {
                    "category_key": str(category_override["key"]),
                    "category_label": str(category_override["label"]),
                    "category_order": int(category_override["order"]),
                    "categories": [
                        {
                            "key": str(category_override["key"]),
                            "label": str(category_override["label"]),
                            "order": int(category_override["order"]),
                        }
                    ],
                }
            )
        resolved_entries.append(enriched)

    return resolved_entries


def build_project_import_preview(
    raw_text: str,
    *,
    preferred_kind: str | None = None,
) -> dict[str, object]:
    """Parse, aggregate and resolve import text for project preview."""

    parsed = parse_project_import_text(raw_text, preferred_kind=preferred_kind)
    aggregated = aggregate_project_import_entries(parsed["entries"])
    resolved_entries = resolve_project_import_entries(aggregated)
    grouped_entries = _group_project_entries(resolved_entries)

    return {
        "source_kind": parsed["source_kind"],
        "source_name": parsed.get("source_name") or "",
        "entries": resolved_entries,
        "groups": grouped_entries,
        "summary": {
            "total_lines": len(parsed["entries"]),
            "total_unique_items": len(resolved_entries),
            "total_quantity": sum(
                int(entry.get("quantity") or 0) for entry in resolved_entries
            ),
            "craftable_items": sum(
                1 for entry in resolved_entries if entry.get("is_craftable")
            ),
            "non_craftable_items": sum(
                1 for entry in resolved_entries if not entry.get("is_craftable")
            ),
            "unresolved_items": sum(
                1 for entry in resolved_entries if not entry.get("resolved")
            ),
        },
    }


def parse_project_me_te_overrides(query_params) -> dict[int, dict[str, int]]:
    """Parse per-blueprint ME/TE overrides from request query params."""

    overrides: dict[int, dict[str, int]] = {}
    for key, value in query_params.items():
        if not value:
            continue
        try:
            numeric_value = int(value)
        except (TypeError, ValueError):
            continue

        if key.startswith("me_"):
            try:
                blueprint_type_id = int(key.replace("me_", "", 1))
            except (TypeError, ValueError):
                continue
            overrides.setdefault(blueprint_type_id, {})["me"] = max(
                0, min(numeric_value, 10)
            )
        elif key.startswith("te_"):
            try:
                blueprint_type_id = int(key.replace("te_", "", 1))
            except (TypeError, ValueError):
                continue
            overrides.setdefault(blueprint_type_id, {})["te"] = max(
                0, min(numeric_value, 20)
            )
    return overrides


def create_project_from_entries(
    *,
    user,
    name: str,
    status: str,
    source_kind: str,
    source_text: str,
    source_name: str,
    selected_entries: Sequence[dict[str, object]],
    notes: str = "",
) -> ProductionProject:
    """Persist a production project with its selected aggregated entries."""

    project = ProductionProject.objects.create(
        user=user,
        name=(name or source_name or "New production project").strip()[:255],
        status=status,
        source_kind=source_kind,
        source_text=source_text,
        source_name=(source_name or "").strip()[:255],
        notes=notes,
        summary={
            "selected_items": len(selected_entries),
            "selected_quantity": sum(
                max(0, int(entry.get("quantity") or 0)) for entry in selected_entries
            ),
            "craftable_items": sum(
                1 for entry in selected_entries if entry.get("is_craftable")
            ),
            "buy_items": sum(
                1 for entry in selected_entries if entry.get("inclusion_mode") == "buy"
            ),
        },
    )

    item_rows: list[ProductionProjectItem] = []
    for entry in selected_entries:
        inclusion_mode = str(entry.get("inclusion_mode") or "produce")
        item_rows.append(
            ProductionProjectItem(
                project=project,
                type_id=entry.get("type_id"),
                type_name=str(entry.get("type_name") or "").strip()[:255],
                quantity_requested=max(1, int(entry.get("quantity") or 1)),
                category_key=str(entry.get("category_key") or "")[:64],
                category_label=str(entry.get("category_label") or "")[:255],
                category_order=_coerce_category_order(entry.get("category_order")),
                source_line=str(entry.get("source_line") or "")[:65535],
                is_selected=True,
                is_craftable=bool(entry.get("is_craftable")),
                inclusion_mode=inclusion_mode,
                blueprint_type_id=entry.get("blueprint_type_id"),
                metadata={
                    "group_name": entry.get("group_name") or "",
                    "categories": entry.get("categories") or [],
                    "source_lines": entry.get("source_lines") or [],
                    "not_craftable_reason": entry.get("not_craftable_reason"),
                },
            )
        )
    if item_rows:
        ProductionProjectItem.objects.bulk_create(item_rows)
    return project


def _get_blueprint_output_quantity(blueprint_type_id: int) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT quantity
            FROM indy_hub_sdeindustryactivityproduct
            WHERE eve_type_id = %s AND activity_id IN (1, 11)
            ORDER BY CASE activity_id WHEN 1 THEN 0 WHEN 11 THEN 1 ELSE 99 END
            LIMIT 1
            """,
            [int(blueprint_type_id)],
        )
        row = cursor.fetchone()
    return max(1, int((row[0] if row else 1) or 1))


def build_legacy_workspace_state(
    simulation: ProductionSimulation,
) -> dict[str, object]:
    return {
        "blueprint_type_id": int(simulation.blueprint_type_id or 0),
        "blueprint_name": simulation.blueprint_name,
        "runs": max(1, int(simulation.runs or 1)),
        "simulation_name": simulation.simulation_name,
        "active_tab": simulation.active_tab or "materials",
        "items": [
            {
                "type_id": int(config.item_type_id),
                "mode": str(config.production_mode or "prod"),
                "quantity": int(config.quantity_needed or 0),
            }
            for config in simulation.production_configs.all().order_by("item_type_id")
        ],
        "blueprint_efficiencies": [
            {
                "blueprint_type_id": int(eff.blueprint_type_id),
                "material_efficiency": int(eff.material_efficiency or 0),
                "time_efficiency": int(eff.time_efficiency or 0),
            }
            for eff in simulation.blueprint_efficiencies.all().order_by(
                "blueprint_type_id"
            )
        ],
        "custom_prices": [
            {
                "item_type_id": int(price.item_type_id),
                "unit_price": float(price.unit_price or 0),
                "is_sale_price": bool(price.is_sale_price),
            }
            for price in simulation.custom_prices.all().order_by("item_type_id")
        ],
        "estimated_cost": float(simulation.estimated_cost or 0),
        "estimated_revenue": float(simulation.estimated_revenue or 0),
        "estimated_profit": float(simulation.estimated_profit or 0),
        "total_items": int(simulation.total_items or 0),
        "total_buy_items": int(simulation.total_buy_items or 0),
        "total_prod_items": int(simulation.total_prod_items or 0),
    }


def create_project_from_single_blueprint(
    *,
    user,
    blueprint_type_id: int,
    blueprint_name: str,
    runs: int = 1,
    name: str = "",
    status: str = ProductionProject.Status.DRAFT,
    me: int = 0,
    te: int = 0,
    active_tab: str = "materials",
    workspace_state: dict[str, object] | None = None,
) -> ProductionProject:
    numeric_blueprint_type_id = int(blueprint_type_id)
    resolved_blueprint_type_id = numeric_blueprint_type_id
    product_type_id = get_blueprint_product_type_id(resolved_blueprint_type_id)
    if not product_type_id:
        resolved_blueprint_type_id = (
            _resolve_blueprints_for_products([numeric_blueprint_type_id]).get(
                numeric_blueprint_type_id
            )
            or numeric_blueprint_type_id
        )
        product_type_id = get_blueprint_product_type_id(resolved_blueprint_type_id)

    safe_runs = max(1, int(runs or 1))
    product_type_name = get_type_name(product_type_id) if product_type_id else ""
    resolved_blueprint_name = (
        get_type_name(resolved_blueprint_type_id)
        or blueprint_name
        or product_type_name
        or str(resolved_blueprint_type_id)
    )
    output_quantity = _get_blueprint_output_quantity(resolved_blueprint_type_id)
    final_quantity = max(1, output_quantity * safe_runs)

    project = create_project_from_entries(
        user=user,
        name=(
            name
            or product_type_name
            or resolved_blueprint_name
            or "New production project"
        ),
        status=(
            status
            if status in ProductionProject.Status.values
            else ProductionProject.Status.DRAFT
        ),
        source_kind=ProductionProject.SourceKind.MANUAL,
        source_text=str(resolved_blueprint_name or "").strip(),
        source_name=str(resolved_blueprint_name or "").strip(),
        selected_entries=[
            {
                "type_id": product_type_id,
                "type_name": product_type_name
                or resolved_blueprint_name
                or str(resolved_blueprint_type_id),
                "quantity": final_quantity,
                "category_key": "manual",
                "category_label": "Manual list",
                "category_order": 90,
                "source_line": product_type_name
                or resolved_blueprint_name
                or str(resolved_blueprint_type_id),
                "is_craftable": True,
                "blueprint_type_id": resolved_blueprint_type_id,
                "inclusion_mode": ProductionProjectItem.InclusionMode.PRODUCE,
            }
        ],
        notes="Migrated from legacy single-blueprint craft flow.",
    )

    persisted_workspace_state = dict(workspace_state or {})
    if not persisted_workspace_state:
        persisted_workspace_state = {
            "blueprint_type_id": resolved_blueprint_type_id,
            "blueprint_name": resolved_blueprint_name,
            "runs": safe_runs,
            "active_tab": active_tab or "materials",
            "items": [],
            "blueprint_efficiencies": [
                {
                    "blueprint_type_id": resolved_blueprint_type_id,
                    "material_efficiency": max(0, min(int(me or 0), 10)),
                    "time_efficiency": max(0, min(int(te or 0), 20)),
                }
            ],
            "custom_prices": [],
        }

    project.workspace_state = persisted_workspace_state
    project.save(update_fields=["workspace_state", "updated_at"])
    return project


def migrate_user_legacy_simulations_to_projects(user) -> dict[str, int]:
    simulations = list(
        ProductionSimulation.objects.filter(user=user)
        .order_by("updated_at")
        .prefetch_related(
            "production_configs", "blueprint_efficiencies", "custom_prices"
        )
    )
    summary = {"migrated": 0, "failed": 0}
    if not simulations:
        return summary

    for simulation in simulations:
        try:
            project_name = simulation.simulation_name or simulation.display_name
            create_project_from_single_blueprint(
                user=user,
                blueprint_type_id=int(simulation.blueprint_type_id),
                blueprint_name=simulation.blueprint_name,
                runs=max(1, int(simulation.runs or 1)),
                name=project_name,
                status=ProductionProject.Status.SAVED,
                workspace_state=build_legacy_workspace_state(simulation),
                active_tab=simulation.active_tab or "materials",
            )
            simulation.delete()
            summary["migrated"] += 1
        except Exception:
            logger.exception(
                "Unable to migrate legacy production simulation %s for user %s",
                simulation.id,
                getattr(user, "id", None),
            )
            summary["failed"] += 1

    return summary


def build_project_workspace_payload(
    project: ProductionProject,
    *,
    skill_cache_ttl=timedelta(hours=1),
    me_te_overrides: dict[int, dict[str, int]] | None = None,
    include_full_structure_options: bool = True,
) -> dict[str, object]:
    """Build a craft workspace payload for a multi-product project."""

    profile_started_at = perf_counter()
    workspace_timing_steps: list[dict[str, object]] = []

    def record_timing_step(step_id: str, label: str, started_at: float) -> None:
        workspace_timing_steps.append(
            {
                "id": step_id,
                "label": label,
                "duration_ms": round((perf_counter() - started_at) * 1000, 1),
            }
        )

    def build_workspace_timing() -> dict[str, object]:
        return {
            "total_ms": round((perf_counter() - profile_started_at) * 1000, 1),
            "steps": workspace_timing_steps,
        }

    overrides = me_te_overrides or {}
    selected_items_started_at = perf_counter()
    selected_items = list(
        project.items.filter(is_selected=True)
        .exclude(inclusion_mode=ProductionProjectItem.InclusionMode.SKIP)
        .order_by("category_order", "id")
    )
    record_timing_step(
        "selected-items", "Project items query", selected_items_started_at
    )

    if not selected_items:
        return {
            "type_id": 0,
            "bp_type_id": 0,
            "project_id": project.id,
            "project_ref": project.project_ref,
            "name": project.name,
            "project_status": project.status,
            "source_kind": project.source_kind,
            "workspace_state": dict(project.workspace_state or {}),
            "product_type_id": None,
            "final_product_qty": 0,
            "materials_tree": [],
            "materials": [],
            "direct_materials": [],
            "materials_by_group": {},
            "market_group_map": {},
            "recipe_map": {},
            "craft_cycles_summary": {},
            "blueprint_configs_grouped": [],
            "production_time_map": {},
            "craft_character_advisor": {"characters": [], "items": {}, "summary": {}},
            "structure_planner": {
                "items": [],
                "structures": [],
                "summary": {"has_structures": False},
            },
            "final_outputs": [],
            "page": {
                "blueprint_configs": [],
                "craft_cycles_summary_static": {},
                "main_bp_info": {},
                "workspace_timing": build_workspace_timing(),
            },
        }

    blueprint_product_cache: dict[int, dict[str, object] | None] = {}
    blueprint_recipe_cache: dict[tuple[int, int], dict[str, object]] = {}
    product_blueprint_cache: dict[int, int | None] = {}
    type_meta_cache: dict[int, tuple[int | None, int | None]] = {}
    type_name_cache_started_at = perf_counter()
    type_name_cache = _resolve_type_names(
        [
            value
            for item in selected_items
            for value in (item.type_id, item.blueprint_type_id)
            if value
        ]
    )
    record_timing_step("type-names", "Resolve type names", type_name_cache_started_at)
    market_group_cache: dict[int, dict[str, object]] = {}
    recipe_map: dict[int, dict[str, object]] = {}

    def get_type_meta(type_id: int) -> tuple[int | None, int | None]:
        numeric_type_id = int(type_id or 0)
        if numeric_type_id <= 0:
            return (None, None)
        if numeric_type_id in type_meta_cache:
            return type_meta_cache[numeric_type_id]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.meta_group_id_raw, g.category_id
                FROM eve_sde_itemtype t
                LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
                WHERE t.id = %s
                LIMIT 1
                """,
                [numeric_type_id],
            )
            row = cursor.fetchone()

        type_meta_cache[numeric_type_id] = (
            int(row[0]) if row and row[0] is not None else None,
            int(row[1]) if row and row[1] is not None else None,
        )
        return type_meta_cache[numeric_type_id]

    def get_market_group_info(type_id: int) -> dict[str, object]:
        numeric_type_id = int(type_id or 0)
        if numeric_type_id <= 0:
            return {"group_name": "", "group_id": None}
        if numeric_type_id in market_group_cache:
            return market_group_cache[numeric_type_id]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(g.name, ''), g.id
                FROM eve_sde_itemtype t
                LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
                WHERE t.id = %s
                LIMIT 1
                """,
                [numeric_type_id],
            )
            row = cursor.fetchone()

        market_group_cache[numeric_type_id] = {
            "group_name": str(row[0] or "") if row else "",
            "group_id": int(row[1]) if row and row[1] is not None else None,
        }
        return market_group_cache[numeric_type_id]

    def get_blueprint_product_row(blueprint_type_id: int) -> dict[str, object] | None:
        numeric_blueprint_type_id = int(blueprint_type_id or 0)
        if numeric_blueprint_type_id <= 0:
            return None
        if numeric_blueprint_type_id in blueprint_product_cache:
            return blueprint_product_cache[numeric_blueprint_type_id]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.product_eve_type_id,
                    COALESCE(t.name_en, t.name),
                    p.quantity,
                    p.activity_id,
                    COALESCE(g.name, ''),
                    g.id
                FROM indy_hub_sdeindustryactivityproduct p
                JOIN eve_sde_itemtype t ON t.id = p.product_eve_type_id
                LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
                WHERE p.eve_type_id = %s AND p.activity_id IN (1, 11)
                ORDER BY CASE p.activity_id WHEN 1 THEN 0 WHEN 11 THEN 1 ELSE 99 END
                LIMIT 1
                """,
                [numeric_blueprint_type_id],
            )
            row = cursor.fetchone()

        blueprint_product_cache[numeric_blueprint_type_id] = (
            {
                "product_type_id": int(row[0]),
                "product_type_name": str(row[1] or ""),
                "produced_per_cycle": int(row[2] or 1),
                "activity_id": int(row[3] or 1),
                "group_name": str(row[4] or ""),
                "group_id": int(row[5]) if row[5] is not None else None,
            }
            if row
            else None
        )
        return blueprint_product_cache[numeric_blueprint_type_id]

    def get_blueprint_for_product(product_type_id: int) -> int | None:
        numeric_product_type_id = int(product_type_id or 0)
        if numeric_product_type_id <= 0:
            return None
        if numeric_product_type_id in product_blueprint_cache:
            return product_blueprint_cache[numeric_product_type_id]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT MIN(eve_type_id)
                FROM indy_hub_sdeindustryactivityproduct
                WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                """,
                [numeric_product_type_id],
            )
            row = cursor.fetchone()

        product_blueprint_cache[numeric_product_type_id] = (
            int(row[0]) if row and row[0] is not None else None
        )
        return product_blueprint_cache[numeric_product_type_id]

    def material_efficiency_applies(
        blueprint_type_id: int,
        material_type_id: int,
    ) -> bool:
        blueprint_product = get_blueprint_product_row(blueprint_type_id)
        if not blueprint_product:
            return True

        parent_meta_group_id, parent_category_id = get_type_meta(
            int(blueprint_product["product_type_id"])
        )
        material_meta_group_id, material_category_id = get_type_meta(material_type_id)
        return not is_base_item_material_efficiency_exempt(
            parent_meta_group_id,
            parent_category_id,
            material_meta_group_id,
            material_category_id,
        )

    def build_recipe_entry(
        blueprint_type_id: int,
        blueprint_me: int = 0,
    ) -> dict[str, object]:
        cache_key = (int(blueprint_type_id), int(blueprint_me or 0))
        if cache_key in blueprint_recipe_cache:
            return blueprint_recipe_cache[cache_key]

        product_row = get_blueprint_product_row(blueprint_type_id) or {}
        produced_per_cycle = int(product_row.get("produced_per_cycle") or 1)
        adjusted_inputs = []
        me0_inputs = []
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT material_eve_type_id, quantity
                FROM indy_hub_sdeindustryactivitymaterial
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                """,
                [int(blueprint_type_id)],
            )
            for material_type_id, base_quantity in cursor.fetchall():
                raw_qty = int(base_quantity or 0)
                if raw_qty <= 0:
                    continue
                adjusted_qty = ceil(raw_qty * (100 - int(blueprint_me or 0)) / 100)
                if adjusted_qty > 0:
                    adjusted_inputs.append(
                        {
                            "type_id": int(material_type_id),
                            "quantity": int(adjusted_qty),
                        }
                    )
                me0_inputs.append(
                    {"type_id": int(material_type_id), "quantity": int(raw_qty)}
                )

        blueprint_recipe_cache[cache_key] = {
            "produced_per_cycle": produced_per_cycle,
            "inputs_per_cycle": adjusted_inputs,
            "inputs_per_cycle_me0": me0_inputs,
        }
        return blueprint_recipe_cache[cache_key]

    def build_project_output_node(
        *,
        type_id: int | None,
        type_name: str,
        quantity_needed: int,
        blueprint_type_id: int | None,
        source_item: ProductionProjectItem,
        seen: set[int] | None = None,
    ) -> dict[str, object]:
        numeric_quantity = max(1, int(quantity_needed or 1))
        numeric_type_id = int(type_id or 0) if type_id else None
        numeric_blueprint_type_id = (
            int(blueprint_type_id or 0) if blueprint_type_id else 0
        )
        if numeric_blueprint_type_id > 0 and not get_blueprint_product_row(
            numeric_blueprint_type_id
        ):
            normalized_blueprint_type_id = get_blueprint_for_product(
                numeric_type_id or numeric_blueprint_type_id
            )
            if normalized_blueprint_type_id:
                numeric_blueprint_type_id = int(normalized_blueprint_type_id)
        market_group = get_market_group_info(numeric_type_id or 0)

        if not numeric_blueprint_type_id:
            return {
                "type_id": numeric_type_id,
                "type_name": type_name,
                "quantity": numeric_quantity,
                "cycles": None,
                "produced_per_cycle": None,
                "total_produced": None,
                "surplus": None,
                "market_group": market_group["group_name"],
                "market_group_id": market_group["group_id"],
                "project_inclusion_mode": source_item.inclusion_mode,
                "sub_materials": [],
            }
        current_seen = set(seen or set())
        if numeric_blueprint_type_id in current_seen:
            logger.warning(
                "Stopping recursive craft project expansion for blueprint %s to avoid cycle.",
                numeric_blueprint_type_id,
            )
            return {
                "type_id": numeric_type_id,
                "type_name": type_name,
                "quantity": numeric_quantity,
                "cycles": 0,
                "produced_per_cycle": 0,
                "total_produced": 0,
                "surplus": 0,
                "market_group": market_group["group_name"],
                "market_group_id": market_group["group_id"],
                "project_inclusion_mode": source_item.inclusion_mode,
                "sub_materials": [],
            }

        current_seen.add(numeric_blueprint_type_id)
        product_row = get_blueprint_product_row(numeric_blueprint_type_id) or {}
        produced_per_cycle = max(1, int(product_row.get("produced_per_cycle") or 1))
        cycles = max(1, ceil(numeric_quantity / produced_per_cycle))
        total_produced = cycles * produced_per_cycle
        surplus = max(0, total_produced - numeric_quantity)
        blueprint_me = int(
            (overrides.get(numeric_blueprint_type_id) or {}).get("me") or 0
        )

        recipe_map.setdefault(
            int(numeric_type_id or product_row.get("product_type_id") or 0),
            build_recipe_entry(numeric_blueprint_type_id, blueprint_me),
        )

        children: list[dict[str, object]] = []
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.material_eve_type_id, COALESCE(t.name_en, t.name), m.quantity
                FROM indy_hub_sdeindustryactivitymaterial m
                JOIN eve_sde_itemtype t ON t.id = m.material_eve_type_id
                WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                """,
                [numeric_blueprint_type_id],
            )
            material_rows = list(cursor.fetchall())

        for material_type_id, material_name, base_quantity in material_rows:
            material_type_id = int(material_type_id)
            apply_material_efficiency = material_efficiency_applies(
                numeric_blueprint_type_id,
                material_type_id,
            )
            child_quantity = compute_job_material_quantity(
                base_quantity,
                cycles,
                blueprint_me,
                apply_material_efficiency=apply_material_efficiency,
            )
            child_blueprint_type_id = get_blueprint_for_product(material_type_id)
            child_item = source_item
            child_node = build_project_output_node(
                type_id=material_type_id,
                type_name=str(
                    material_name
                    or type_name_cache.get(material_type_id)
                    or material_type_id
                ),
                quantity_needed=child_quantity,
                blueprint_type_id=child_blueprint_type_id,
                source_item=child_item,
                seen=current_seen,
            )
            child_node["material_bonus_applicable"] = apply_material_efficiency
            children.append(child_node)

        return {
            "type_id": numeric_type_id or int(product_row.get("product_type_id") or 0),
            "type_name": type_name or str(product_row.get("product_type_name") or ""),
            "quantity": numeric_quantity,
            "cycles": cycles,
            "produced_per_cycle": produced_per_cycle,
            "total_produced": total_produced,
            "surplus": surplus,
            "blueprint_type_id": numeric_blueprint_type_id,
            "market_group": market_group["group_name"]
            or str(product_row.get("group_name") or ""),
            "market_group_id": (
                market_group["group_id"]
                if market_group["group_id"] is not None
                else product_row.get("group_id")
            ),
            "project_inclusion_mode": source_item.inclusion_mode,
            "sub_materials": children,
        }

    root_nodes_started_at = perf_counter()
    root_nodes = [
        build_project_output_node(
            type_id=item.type_id,
            type_name=item.type_name,
            quantity_needed=item.quantity_requested,
            blueprint_type_id=item.blueprint_type_id,
            source_item=item,
        )
        for item in selected_items
    ]
    record_timing_step("materials-tree", "Build materials tree", root_nodes_started_at)

    cycle_summary: dict[int, dict[str, object]] = {}
    all_type_ids: set[int] = set()

    def register_node(node: dict[str, object]) -> None:
        type_id = int(node.get("type_id") or 0) if node.get("type_id") else 0
        if type_id > 0:
            all_type_ids.add(type_id)
        blueprint_type_id = int(node.get("blueprint_type_id") or 0)
        if blueprint_type_id > 0:
            type_name = str(node.get("type_name") or type_name_cache.get(type_id) or "")
            group_name = str(node.get("market_group") or "")
            entry = cycle_summary.setdefault(
                type_id,
                {
                    "type_id": type_id,
                    "type_name": type_name,
                    "market_group": group_name,
                    "total_needed": 0,
                    "produced_per_cycle": max(
                        1, int(node.get("produced_per_cycle") or 1)
                    ),
                },
            )
            entry["total_needed"] = int(entry.get("total_needed") or 0) + int(
                node.get("quantity") or 0
            )
        for child in node.get("sub_materials", []):
            register_node(child)

    cycle_summary_started_at = perf_counter()
    for node in root_nodes:
        register_node(node)

    for type_id, entry in cycle_summary.items():
        produced_per_cycle = max(1, int(entry.get("produced_per_cycle") or 1))
        total_needed = max(0, int(entry.get("total_needed") or 0))
        cycles = ceil(total_needed / produced_per_cycle) if total_needed > 0 else 0
        total_produced = produced_per_cycle * cycles
        entry["cycles"] = cycles
        entry["total_produced"] = total_produced
        entry["surplus"] = max(0, total_produced - total_needed)
    record_timing_step(
        "cycle-summary", "Aggregate production cycles", cycle_summary_started_at
    )

    market_groups_started_at = perf_counter()
    market_group_map = _resolve_market_groups(all_type_ids)
    for type_id, entry in cycle_summary.items():
        if not entry.get("market_group"):
            info = (
                market_group_map.get(type_id)
                or market_group_map.get(str(type_id))
                or {}
            )
            entry["market_group"] = info.get("group_name") or ""
    record_timing_step(
        "market-groups", "Resolve market groups", market_groups_started_at
    )

    missing_blueprint_name_ids = sorted(
        {
            int(blueprint_type_id)
            for blueprint_type_id in product_blueprint_cache.values()
            if int(blueprint_type_id or 0) > 0
            and int(blueprint_type_id) not in type_name_cache
        }
    )
    if missing_blueprint_name_ids:
        missing_blueprint_names_started_at = perf_counter()
        type_name_cache.update(_resolve_type_names(missing_blueprint_name_ids))
        record_timing_step(
            "blueprint-names",
            "Resolve missing blueprint names",
            missing_blueprint_names_started_at,
        )

    production_time_started_at = perf_counter()
    production_time_map = build_craft_time_map(
        recipe_map=recipe_map,
        product_type_id=None,
        product_type_name="",
        product_output_per_cycle=0,
        root_blueprint_type_id=None,
    )
    record_timing_step(
        "production-times", "Build production time map", production_time_started_at
    )

    character_advisor_started_at = perf_counter()
    craft_character_advisor = build_craft_character_advisor(
        user=project.user,
        production_time_map=production_time_map,
        skill_cache_ttl=skill_cache_ttl,
    )
    record_timing_step(
        "character-advisor", "Build character advisor", character_advisor_started_at
    )

    structure_planner_started_at = perf_counter()
    structure_planner = build_craft_structure_planner(
        product_type_id=None,
        product_type_name="",
        product_output_per_cycle=0,
        craft_cycles_summary=cycle_summary,
        include_all_options=include_full_structure_options,
    )
    record_timing_step(
        "structure-planner", "Build structure planner", structure_planner_started_at
    )

    blueprint_configs_started_at = perf_counter()
    blueprint_configs, blueprint_configs_grouped = (
        _build_project_blueprint_configs_grouped(
            user=project.user,
            cycle_summary=cycle_summary,
            product_blueprint_cache=product_blueprint_cache,
            overrides=overrides,
            type_name_cache=type_name_cache,
        )
    )
    record_timing_step(
        "blueprint-configs", "Build blueprint configs", blueprint_configs_started_at
    )

    final_outputs = []
    for item, root_node in zip(selected_items, root_nodes):
        resolved_type_id = int(root_node.get("type_id") or item.type_id or 0) or None
        resolved_type_name = str(root_node.get("type_name") or item.type_name or "")
        final_outputs.append(
            {
                "type_id": resolved_type_id,
                "type_name": resolved_type_name,
                "quantity": item.quantity_requested,
                "is_craftable": item.is_craftable,
                "blueprint_type_id": item.blueprint_type_id,
                "inclusion_mode": item.inclusion_mode,
                "category_key": item.category_key,
                "category_label": item.category_label,
            }
        )

    workspace_state = dict(project.workspace_state or {})
    main_blueprint_info = {}
    root_blueprint_type_id = 0
    root_product_type_id = 0
    root_product_output_per_cycle = 0
    root_final_product_qty = 0
    num_runs = max(1, int(workspace_state.get("runs") or 1))
    main_me = 0
    main_te = 0
    direct_materials = []
    materials_by_group = {}

    if len(selected_items) == 1 and root_nodes:
        root_item = selected_items[0]
        root_node = root_nodes[0]
        root_blueprint_type_id = int(
            root_node.get("blueprint_type_id")
            or root_item.blueprint_type_id
            or workspace_state.get("blueprint_type_id")
            or 0
        )
        root_product_type_id = int(root_node.get("type_id") or root_item.type_id or 0)
        root_product_output_per_cycle = max(
            0,
            int(
                root_node.get("produced_per_cycle")
                or _get_blueprint_output_quantity(root_blueprint_type_id)
                or 0
            ),
        )
        root_final_product_qty = max(
            0,
            int(root_item.quantity_requested or root_node.get("quantity") or 0),
        )

        me_te_config = workspace_state.get("meTeConfig")
        if not isinstance(me_te_config, dict):
            me_te_config = {}
        blueprint_configs_state = me_te_config.get("blueprintConfigs")
        if not isinstance(blueprint_configs_state, dict):
            blueprint_configs_state = {}
        root_me_te = blueprint_configs_state.get(str(root_blueprint_type_id))
        if not isinstance(root_me_te, dict):
            root_me_te = {}
        main_me = int(root_me_te.get("me") or me_te_config.get("mainME") or 0)
        main_te = int(root_me_te.get("te") or me_te_config.get("mainTE") or 0)

        root_children = (
            root_node.get("sub_materials")
            if isinstance(root_node.get("sub_materials"), list)
            else []
        )
        direct_materials = [
            {
                "type_id": int(child.get("type_id") or 0),
                "type_name": str(child.get("type_name") or ""),
                "quantity": max(0, int(child.get("quantity") or 0)),
            }
            for child in root_children
            if int(child.get("type_id") or 0) > 0
        ]
        for material in direct_materials:
            material_type_id = int(material["type_id"])
            group_info = (
                market_group_map.get(material_type_id)
                or market_group_map.get(str(material_type_id))
                or {}
            )
            group_id = group_info.get("group_id")
            group_name = str(group_info.get("group_name") or "Other materials")
            group_key = str(group_id if group_id is not None else group_name)
            group_entry = materials_by_group.setdefault(
                group_key,
                {
                    "group_id": group_id,
                    "group_name": group_name,
                    "items": [],
                },
            )
            group_entry["items"].append(material)

        matching_blueprint_config = next(
            (
                blueprint
                for blueprint in blueprint_configs
                if int(blueprint.get("type_id") or 0) == root_blueprint_type_id
            ),
            None,
        )
        if matching_blueprint_config:
            main_blueprint_info = {
                "type_id": root_blueprint_type_id,
                "product_type_id": int(
                    matching_blueprint_config.get("product_type_id")
                    or root_product_type_id
                    or 0
                ),
                "material_efficiency": int(
                    matching_blueprint_config.get("material_efficiency") or main_me or 0
                ),
                "time_efficiency": int(
                    matching_blueprint_config.get("time_efficiency") or main_te or 0
                ),
                "user_material_efficiency": matching_blueprint_config.get(
                    "user_material_efficiency"
                ),
                "user_time_efficiency": matching_blueprint_config.get(
                    "user_time_efficiency"
                ),
                "is_owned": bool(matching_blueprint_config.get("user_owns", False)),
                "user_owns": bool(matching_blueprint_config.get("user_owns", False)),
                "is_copy": bool(matching_blueprint_config.get("is_copy", False)),
                "runs_available": matching_blueprint_config.get("runs_available"),
            }

    return {
        "type_id": root_blueprint_type_id,
        "bp_type_id": root_blueprint_type_id,
        "project_id": project.id,
        "project_ref": project.project_ref,
        "name": project.name,
        "project_status": project.status,
        "source_kind": project.source_kind,
        "workspace_state": workspace_state,
        "product_type_id": root_product_type_id or None,
        "output_qty_per_run": root_product_output_per_cycle,
        "product_output_per_cycle": root_product_output_per_cycle,
        "final_product_qty": root_final_product_qty,
        "num_runs": num_runs,
        "me": main_me,
        "te": main_te,
        "materials_tree": root_nodes,
        "materials": direct_materials,
        "direct_materials": direct_materials,
        "materials_by_group": materials_by_group,
        "market_group_map": market_group_map,
        "recipe_map": recipe_map,
        "craft_cycles_summary": cycle_summary,
        "blueprint_configs_grouped": blueprint_configs_grouped,
        "production_time_map": production_time_map,
        "craft_character_advisor": craft_character_advisor,
        "structure_planner": structure_planner,
        "final_outputs": final_outputs,
        "page": {
            "blueprint_configs": [
                {
                    "type_id": blueprint.get("type_id"),
                    "product_type_id": blueprint.get("product_type_id"),
                    "material_efficiency": blueprint.get("material_efficiency", 0),
                    "time_efficiency": blueprint.get("time_efficiency", 0),
                    "user_material_efficiency": blueprint.get(
                        "user_material_efficiency"
                    ),
                    "user_time_efficiency": blueprint.get("user_time_efficiency"),
                    "is_owned": blueprint.get("user_owns", False),
                    "user_owns": blueprint.get("user_owns", False),
                    "is_copy": blueprint.get("is_copy", False),
                    "runs_available": blueprint.get("runs_available"),
                    "shared_copies_available": bool(
                        blueprint.get("shared_copies_available", [])
                    ),
                }
                for blueprint in blueprint_configs
            ],
            "craft_cycles_summary_static": {
                str(type_id): {
                    "type_id": entry.get("type_id"),
                    "type_name": entry.get("type_name"),
                    "cycles": entry.get("cycles", 0),
                    "total_needed": entry.get("total_needed", 0),
                    "produced_per_cycle": entry.get("produced_per_cycle", 0),
                    "total_produced": entry.get("total_produced", 0),
                    "surplus": entry.get("surplus", 0),
                }
                for type_id, entry in cycle_summary.items()
            },
            "main_bp_info": main_blueprint_info,
            "workspace_timing": build_workspace_timing(),
        },
    }


def _parse_eft_project_import(raw_text: str) -> dict[str, object]:
    lines = [line.rstrip() for line in raw_text.split("\n")]
    first_non_empty_index = next(
        (index for index, line in enumerate(lines) if line.strip()), None
    )
    if first_non_empty_index is None:
        return {"source_kind": "eft", "source_name": "", "entries": []}

    header_line = lines[first_non_empty_index].strip()
    header_match = EFT_HEADER_RE.match(header_line)
    if not header_match:
        return _parse_manual_project_import(raw_text)

    entries: list[dict[str, object]] = [
        _build_entry(
            type_name=header_match.group("hull").strip(),
            quantity=1,
            category=HULL_CATEGORY,
            source_line=header_line,
        )
    ]

    grouped_lines = _split_non_empty_groups(lines[first_non_empty_index + 1 :])
    for group_index, group_lines in enumerate(grouped_lines):
        category = _get_eft_category(group_index)
        for raw_line in group_lines:
            parsed_line = _parse_line_item(raw_line)
            if not parsed_line:
                continue
            entries.append(
                _build_entry(
                    type_name=parsed_line["type_name"],
                    quantity=parsed_line["quantity"],
                    category=category,
                    source_line=raw_line,
                )
            )

    return {
        "source_kind": "eft",
        "source_name": header_match.group("fit_name").strip(),
        "entries": entries,
    }


def _parse_manual_project_import(raw_text: str) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for raw_line in raw_text.splitlines():
        parsed_line = _parse_line_item(raw_line)
        if not parsed_line:
            continue
        entries.append(
            _build_entry(
                type_name=parsed_line["type_name"],
                quantity=parsed_line["quantity"],
                category=MANUAL_CATEGORY,
                source_line=raw_line,
            )
        )

    return {
        "source_kind": "manual",
        "source_name": "",
        "entries": entries,
    }


def _group_project_entries(
    entries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
    for entry in sorted(
        entries,
        key=lambda row: (
            _coerce_category_order(row.get("category_order")),
            str(row.get("category_label") or ""),
            str(row.get("type_name") or ""),
        ),
    ):
        category_key = str(entry.get("category_key") or "other")
        category_label = str(entry.get("category_label") or "Other")
        if category_key not in grouped:
            grouped[category_key] = {
                "key": category_key,
                "label": category_label,
                "order": _coerce_category_order(entry.get("category_order")),
                "items": [],
            }
        grouped[category_key]["items"].append(entry)
    return list(grouped.values())


def _resolve_item_types_by_name(names: Sequence[str]) -> dict[str, dict[str, object]]:
    if not names:
        return {}

    placeholders = ", ".join(["%s"] * len(names))
    lower_names = [name.casefold() for name in names]
    query = f"""
        SELECT t.id, t.name, COALESCE(g.name, '')
        FROM eve_sde_itemtype t
        LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
        WHERE LOWER(t.name) IN ({placeholders})
    """

    resolved: dict[str, dict[str, object]] = {}
    with connection.cursor() as cursor:
        cursor.execute(query, lower_names)
        for type_id, type_name, group_name in cursor.fetchall():
            resolved[str(type_name).casefold()] = {
                "type_id": int(type_id),
                "type_name": str(type_name),
                "group_name": str(group_name or ""),
            }
    return resolved


def _resolve_blueprints_for_products(type_ids: Iterable[int]) -> dict[int, int]:
    type_id_list = [int(type_id) for type_id in type_ids if int(type_id or 0) > 0]
    if not type_id_list:
        return {}

    placeholders = ", ".join(["%s"] * len(type_id_list))
    query = f"""
        SELECT product_eve_type_id, MIN(eve_type_id) AS blueprint_type_id
        FROM indy_hub_sdeindustryactivityproduct
        WHERE activity_id IN (1, 11) AND product_eve_type_id IN ({placeholders})
        GROUP BY product_eve_type_id
    """

    blueprint_map: dict[int, int] = {}
    with connection.cursor() as cursor:
        cursor.execute(query, type_id_list)
        for product_type_id, blueprint_type_id in cursor.fetchall():
            if product_type_id is None or blueprint_type_id is None:
                continue
            blueprint_map[int(product_type_id)] = int(blueprint_type_id)
    return blueprint_map


def _split_non_empty_groups(lines: Sequence[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current_group: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_group:
                groups.append(current_group)
                current_group = []
            continue
        current_group.append(line)
    if current_group:
        groups.append(current_group)
    return groups


def _parse_line_item(raw_line: str) -> dict[str, object] | None:
    line = str(raw_line or "").strip()
    if not line:
        return None

    suffix_match = QUANTITY_SUFFIX_RE.match(line)
    if suffix_match:
        return {
            "type_name": suffix_match.group("name").strip(),
            "quantity": max(1, int(suffix_match.group("quantity"))),
        }

    prefix_match = QUANTITY_PREFIX_RE.match(line)
    if prefix_match:
        return {
            "type_name": prefix_match.group("name").strip(),
            "quantity": max(1, int(prefix_match.group("quantity"))),
        }

    return {"type_name": line, "quantity": 1}


def _build_entry(
    *,
    type_name: str,
    quantity: int,
    category: dict[str, object],
    source_line: str,
) -> dict[str, object]:
    return {
        "type_name": str(type_name).strip(),
        "quantity": max(1, int(quantity or 1)),
        "category_key": str(category["key"]),
        "category_label": str(category["label"]),
        "category_order": int(category["order"]),
        "source_line": str(source_line).strip(),
    }


def _get_eft_category(group_index: int) -> dict[str, object]:
    if group_index < len(EFT_CATEGORY_SPECS):
        return EFT_CATEGORY_SPECS[group_index]
    return {
        "key": f"eft_group_{group_index}",
        "label": f"Group {group_index + 1}",
        "order": 100 + group_index,
    }


def _normalize_resolved_eft_category(
    entry: dict[str, object],
    group_name: str,
) -> dict[str, object] | None:
    category_key = str(entry.get("category_key") or "")
    if category_key not in {"subsystems", "drone_bay"}:
        return None

    normalized_group_name = str(group_name or "").casefold()
    type_name = str(entry.get("type_name") or "")
    source_line = str(entry.get("source_line") or "")
    is_drone_group = any(
        keyword in normalized_group_name for keyword in DRONE_GROUP_KEYWORDS
    )
    is_subsystem_group = any(
        keyword in normalized_group_name for keyword in SUBSYSTEM_GROUP_KEYWORDS
    )
    if not is_subsystem_group and _looks_like_strategic_cruiser_subsystem(
        type_name=type_name,
        source_line=source_line,
    ):
        is_subsystem_group = True

    if category_key == "subsystems":
        if is_drone_group:
            return EFT_CATEGORY_SPEC_BY_KEY["drone_bay"]
        if not is_subsystem_group:
            return EFT_CATEGORY_SPEC_BY_KEY["cargo"]
        return None

    if is_subsystem_group:
        return EFT_CATEGORY_SPEC_BY_KEY["subsystems"]
    if not is_drone_group:
        return EFT_CATEGORY_SPEC_BY_KEY["cargo"]
    return None


def _looks_like_strategic_cruiser_subsystem(
    *, type_name: str, source_line: str
) -> bool:
    candidate_texts = [str(type_name or ""), str(source_line or "")]
    return any(
        STRATEGIC_CRUISER_SUBSYSTEM_NAME_RE.search(text)
        for text in candidate_texts
        if text
    )


def _resolve_type_names(type_ids: Iterable[int | None]) -> dict[int, str]:
    numeric_ids = sorted(
        {int(type_id) for type_id in type_ids if int(type_id or 0) > 0}
    )
    if not numeric_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(numeric_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, COALESCE(name_en, name)
            FROM eve_sde_itemtype
            WHERE id IN ({placeholders})
            """,
            numeric_ids,
        )
        return {
            int(type_id): str(type_name or "")
            for type_id, type_name in cursor.fetchall()
        }


def _resolve_market_groups(type_ids: Iterable[int]) -> dict[int, dict[str, object]]:
    numeric_ids = sorted(
        {int(type_id) for type_id in type_ids if int(type_id or 0) > 0}
    )
    if not numeric_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(numeric_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT t.id, COALESCE(g.name, ''), g.id
            FROM eve_sde_itemtype t
            LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
            WHERE t.id IN ({placeholders})
            """,
            numeric_ids,
        )
        return {
            int(type_id): {
                "group_name": str(group_name or ""),
                "group_id": int(group_id) if group_id is not None else None,
            }
            for type_id, group_name, group_id in cursor.fetchall()
        }


def _resolve_user_blueprint_inventory(
    *,
    user,
    blueprint_type_ids: Iterable[int | None],
) -> dict[int, dict[str, object]]:
    numeric_ids = sorted(
        {int(type_id) for type_id in blueprint_type_ids if int(type_id or 0) > 0}
    )
    if not numeric_ids:
        return {}

    user_blueprints = (
        Blueprint.objects.filter(
            owner_user=user,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            type_id__in=numeric_ids,
        )
        .values_list(
            "type_id",
            "material_efficiency",
            "time_efficiency",
            "bp_type",
            "runs",
        )
        .order_by("type_id", "-material_efficiency", "-time_efficiency")
    )

    user_bp_map: dict[int, dict[str, object]] = {}
    for bp_type_id, bp_me, bp_te, bp_type, runs in user_blueprints:
        entry = user_bp_map.setdefault(
            int(bp_type_id),
            {
                "original": None,
                "best_copy": None,
                "copy_runs_total": 0,
            },
        )

        if bp_type == Blueprint.BPType.ORIGINAL:
            if not entry["original"]:
                entry["original"] = {"me": int(bp_me or 0), "te": int(bp_te or 0)}
            else:
                current = entry["original"]
                if bp_me > current["me"] or (
                    bp_me == current["me"] and bp_te > current["te"]
                ):
                    entry["original"] = {"me": int(bp_me or 0), "te": int(bp_te or 0)}
        else:
            entry["copy_runs_total"] = int(entry.get("copy_runs_total") or 0) + int(
                runs or 0
            )
            if not entry["best_copy"]:
                entry["best_copy"] = {"me": int(bp_me or 0), "te": int(bp_te or 0)}
            else:
                current = entry["best_copy"]
                if bp_me > current["me"] or (
                    bp_me == current["me"] and bp_te > current["te"]
                ):
                    entry["best_copy"] = {"me": int(bp_me or 0), "te": int(bp_te or 0)}

    return user_bp_map


def _build_project_blueprint_configs_grouped(
    *,
    user,
    cycle_summary: dict[int, dict[str, object]],
    product_blueprint_cache: dict[int, int | None],
    overrides: dict[int, dict[str, int]],
    type_name_cache: dict[int, str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    user_bp_map = _resolve_user_blueprint_inventory(
        user=user,
        blueprint_type_ids=product_blueprint_cache.values(),
    )
    blueprints = []
    for type_id, entry in sorted(
        cycle_summary.items(),
        key=lambda item: str(item[1].get("type_name") or ""),
    ):
        blueprint_type_id = product_blueprint_cache.get(int(type_id))
        if not blueprint_type_id:
            continue
        override = overrides.get(int(blueprint_type_id), {})
        user_entry = user_bp_map.get(int(blueprint_type_id), {})
        original = user_entry.get("original") or None
        best_copy = user_entry.get("best_copy") or None
        user_owns = bool(original or best_copy)
        is_copy = bool(best_copy) and not bool(original)
        runs_available = (
            int(user_entry.get("copy_runs_total") or 0) if is_copy else None
        )
        user_material_efficiency = (
            int((original or best_copy or {}).get("me") or 0) if user_owns else None
        )
        user_time_efficiency = (
            int((original or best_copy or {}).get("te") or 0) if user_owns else None
        )
        default_me = (
            user_material_efficiency if user_material_efficiency is not None else 0
        )
        default_te = user_time_efficiency if user_time_efficiency is not None else 0
        blueprints.append(
            {
                "type_id": int(blueprint_type_id),
                "type_name": type_name_cache.get(int(blueprint_type_id))
                or f"Blueprint {blueprint_type_id}",
                "material_efficiency": (
                    int(override["me"]) if "me" in override else default_me
                ),
                "time_efficiency": (
                    int(override["te"]) if "te" in override else default_te
                ),
                "user_material_efficiency": user_material_efficiency,
                "user_time_efficiency": user_time_efficiency,
                "user_owns": user_owns,
                "is_copy": is_copy,
                "runs_available": runs_available,
                "shared_copies_available": [],
                "product_type_id": int(type_id),
                "product_type_name": str(entry.get("type_name") or ""),
                "total_needed": int(entry.get("total_needed") or 0),
            }
        )

    if not blueprints:
        return ([], [])

    return (
        blueprints,
        [
            {
                "group_name": "Project blueprints",
                "levels": [{"level": 0, "blueprints": blueprints}],
            }
        ],
    )


def _coerce_category_order(value: object) -> int:
    try:
        if value is None:
            return 999
        return int(value)
    except (TypeError, ValueError):
        return 999
