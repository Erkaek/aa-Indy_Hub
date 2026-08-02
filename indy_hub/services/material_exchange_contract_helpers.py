"""Shared helper functions for material exchange contract workflows."""

# Standard Library
from decimal import Decimal

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from ..models import MaterialExchangeBuyOrder, MaterialExchangeStock
from ..utils.eve import PLACEHOLDER_PREFIX, resolve_location_name
from ..utils.material_exchange_transactions import upsert_material_exchange_transaction

logger = get_extension_logger(__name__)

# Cache for structure names to avoid repeated lookups within a task run.
_structure_name_cache: dict[int, str | None] = {}


def log_sell_order_transactions(order) -> None:
    _transaction, created = upsert_material_exchange_transaction(order)
    if not created:
        return

    for item in order.items.all():
        stock_item, _created = MaterialExchangeStock.objects.get_or_create(
            config=order.config,
            type_id=item.type_id,
            defaults={"type_name": item.type_name},
        )
        stock_item.quantity += item.quantity
        stock_item.save()


def log_buy_order_transactions(order: MaterialExchangeBuyOrder) -> None:
    _transaction, created = upsert_material_exchange_transaction(order)
    if not created:
        return

    for item in order.items.all():
        try:
            stock_item = order.config.stock_items.get(type_id=item.type_id)
            stock_item.quantity = max(stock_item.quantity - item.quantity, 0)
            stock_item.save()
        except MaterialExchangeStock.DoesNotExist:
            continue


def is_transient_esi_error(exc) -> bool:
    try:
        status_code = int(getattr(exc, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status_code = 0
    return 500 <= status_code < 600


def normalize_esi_mapping(payload, *, context: str, logger) -> dict | None:
    """Return a dict from an ESI payload or None if unsupported."""
    if isinstance(payload, dict):
        return payload
    for attr in ("model_dump", "dict", "to_dict"):
        converter = getattr(payload, attr, None)
        if callable(converter):
            try:
                result = converter()
            except Exception:  # pragma: no cover - defensive
                result = None
            if isinstance(result, dict):
                return result
    logger.warning(
        "Unexpected %s payload type for material exchange contracts: %s",
        context,
        type(payload).__name__,
    )
    return None


def is_placeholder_location_name(name: str | None) -> bool:
    return not name or str(name).startswith(PLACEHOLDER_PREFIX)


def normalize_location_match_name(name: str | None) -> str | None:
    if is_placeholder_location_name(name):
        return None

    normalized = str(name).strip()
    if not normalized:
        return None

    if " > " in normalized:
        normalized = normalized.split(" > ", 1)[0].strip()

    return normalized.casefold() or None


def get_location_name(location_id: int | None) -> str | None:
    """Resolve a contract location name using the shared cache-aware resolver."""

    if not location_id:
        return None

    try:
        normalized_location_id = int(location_id)
    except (TypeError, ValueError):
        return None

    if normalized_location_id in _structure_name_cache:
        return _structure_name_cache[normalized_location_id]

    name: str | None = None

    try:
        direct_name = resolve_location_name(
            normalized_location_id,
            force_refresh=False,
            allow_public=True,
        )
        if not is_placeholder_location_name(direct_name):
            name = str(direct_name)
    except Exception:
        logger.debug(
            "Shared location resolver failed for %s",
            normalized_location_id,
            exc_info=True,
        )

    _structure_name_cache[normalized_location_id] = name
    return name


def get_config_locations(config) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    try:
        for location in config.accepted_locations.all().order_by("sort_order", "id"):
            rows.append(
                {
                    "structure_id": int(location.structure_id),
                    "structure_name": str(location.structure_name or ""),
                    "hangar_division": int(location.hangar_division),
                }
            )
    except Exception:
        rows = []

    if rows:
        return rows

    structure_id = getattr(config, "structure_id", None)
    hangar_division = getattr(config, "hangar_division", None)
    if not structure_id or not hangar_division:
        return []

    return [
        {
            "structure_id": int(structure_id),
            "structure_name": str(getattr(config, "structure_name", "") or ""),
            "hangar_division": int(hangar_division),
        }
    ]


def get_config_location_ids(config) -> set[int]:
    location_ids: set[int] = set()
    for location in get_config_locations(config):
        try:
            structure_id = int(location.get("structure_id") or 0)
        except (TypeError, ValueError):
            structure_id = 0
        if structure_id > 0:
            location_ids.add(structure_id)
    return location_ids


def get_config_location_summary(config) -> str:
    labels: list[str] = []
    for location in get_config_locations(config):
        structure_id = int(location.get("structure_id") or 0)
        structure_name = str(location.get("structure_name") or "").strip()
        hangar_division = int(location.get("hangar_division") or 0)
        label = structure_name or f"Structure {structure_id}"
        if hangar_division > 0:
            label = f"{label} / Hangar {hangar_division}"
        labels.append(label)
    return ", ".join(label for label in labels if label)


def get_config_location_match_names(config) -> set[str]:
    names: set[str] = set()

    for location in get_config_locations(config):
        configured_name = normalize_location_match_name(location.get("structure_name"))
        if configured_name:
            names.add(configured_name)

        resolved_name = normalize_location_match_name(
            get_location_name(location.get("structure_id"))
        )
        if resolved_name:
            names.add(resolved_name)

    return names