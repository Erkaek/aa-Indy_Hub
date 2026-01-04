"""
Material Exchange Celery tasks for stock sync, pricing, and payment verification.
"""

# Standard Library
import logging
from decimal import Decimal

# Third Party
from celery import shared_task

# Django
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

# Alliance Auth
from esi.models import Token

# AA Example App
from indy_hub.models import (
    CachedCharacterAsset,
    MaterialExchangeConfig,
    MaterialExchangeStock,
)
from indy_hub.services.asset_cache import (
    force_refresh_corp_assets,
    get_corp_assets_cached,
    get_office_folder_item_id_from_assets,
)
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    shared_client,
)
from indy_hub.utils.eve import get_type_name

logger = logging.getLogger(__name__)


def _me_sell_assets_progress_key(user_id: int) -> str:
    return f"indy_hub:material_exchange:sell_assets_refresh:{int(user_id)}"


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    rate_limit="100/m",
    time_limit=300,
    soft_time_limit=280,
)
def refresh_corp_assets_cached(corporation_id: int) -> None:
    """Refresh corp assets cache and structure names for a given corporation."""
    # AA Example App
    from indy_hub.models import CachedCorporationAsset
    from indy_hub.services.asset_cache import (
        _cache_corp_structure_names,
        resolve_structure_names,
    )

    try:
        logger.info("Refreshing corp assets for corporation %s", corporation_id)
        force_refresh_corp_assets(int(corporation_id))
        logger.info(
            "Successfully refreshed corp assets for corporation %s", corporation_id
        )

        # Also refresh structure names using corp structures endpoint
        logger.info("Caching structure names for corporation %s", corporation_id)
        _cache_corp_structure_names(int(corporation_id))
        logger.info(
            "Successfully cached structure names for corporation %s", corporation_id
        )

        # Force resolution of ALL structure IDs found in corp assets
        # This will use /universe/structures/{id} and /universe/names/ as fallback
        structure_ids = list(
            CachedCorporationAsset.objects.filter(corporation_id=int(corporation_id))
            .values_list("location_id", flat=True)
            .distinct()
        )
        if structure_ids:
            logger.info(
                "Resolving %s unique structure names for corporation %s",
                len(structure_ids),
                corporation_id,
            )
            # Pass corporation_id but not user - will use any corp token with required scope
            resolve_structure_names(
                structure_ids, character_id=None, corporation_id=int(corporation_id)
            )
            logger.info(
                "Successfully resolved structure names for corporation %s",
                corporation_id,
            )

    except Exception as exc:
        logger.exception(
            "Failed to refresh corp assets/structures for corporation %s: %s",
            corporation_id,
            exc,
        )
        raise


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    rate_limit="100/m",
    time_limit=300,
    soft_time_limit=280,
)
def refresh_material_exchange_sell_user_assets(user_id: int) -> None:
    """Refresh CachedCharacterAsset for all of a user's characters, tracking progress.

    Progress is stored in the Django cache and consumed by the sell page.
    """

    logger.info("Starting asset refresh task for user %s", user_id)

    progress_key = _me_sell_assets_progress_key(int(user_id))
    ttl_seconds = 10 * 60

    UserModel = get_user_model()

    try:
        user = UserModel.objects.get(pk=int(user_id))
    except Exception:
        cache.set(
            progress_key,
            {
                "running": False,
                "finished": True,
                "error": "user_not_found",
                "total": 0,
                "done": 0,
            },
            ttl_seconds,
        )
        return

    # Progress should reflect *all* of the user's characters.
    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership

        character_ids = list(
            CharacterOwnership.objects.filter(user=user)
            .values_list("character__character_id", flat=True)
            .distinct()
        )
        character_ids = [int(cid) for cid in character_ids if cid]
    except Exception:
        character_ids = []

    total = int(len(character_ids))
    cache.set(
        progress_key,
        {
            "running": True,
            "finished": False,
            "error": None,
            "total": total,
            "done": 0,
            "failed": 0,
        },
        ttl_seconds,
    )

    if total <= 0:
        cache.set(
            progress_key,
            {
                "running": False,
                "finished": True,
                "error": "no_characters",
                "total": 0,
                "done": 0,
                "failed": 0,
            },
            ttl_seconds,
        )
        return

    now = timezone.now()

    done = 0
    failed = 0
    all_rows: list[CachedCharacterAsset] = []

    for character_id in character_ids:
        if not character_id:
            failed += 1
            done += 1
            cache.set(
                progress_key,
                {
                    "running": True,
                    "finished": False,
                    "error": None,
                    "total": total,
                    "done": done,
                    "failed": failed,
                },
                ttl_seconds,
            )
            continue

        try:
            # Only use a token actually owned by this user.
            # Otherwise Token.get_token() could (in edge cases) resolve a token from
            # another user that happens to have the same character_id.
            if (
                not Token.objects.filter(user=user, character_id=int(character_id))
                .require_scopes(["esi-assets.read_assets.v1"])
                .exists()
            ):
                raise ESITokenError(
                    f"No assets token for character {character_id} and user {user_id}"
                )

            assets = shared_client.fetch_character_assets(
                character_id=int(character_id)
            )
        except (ESITokenError, ESIRateLimitError, ESIForbiddenError, ESIClientError):
            failed += 1
            done += 1
            cache.set(
                progress_key,
                {
                    "running": True,
                    "finished": False,
                    "error": None,
                    "total": total,
                    "done": done,
                    "failed": failed,
                },
                ttl_seconds,
            )
            continue

        rows: list[CachedCharacterAsset] = []
        for asset in assets or []:
            rows.append(
                CachedCharacterAsset(
                    user=user,
                    character_id=int(character_id),
                    location_id=int(asset.get("location_id", 0) or 0),
                    location_flag=str(asset.get("location_flag", "") or ""),
                    type_id=int(asset.get("type_id", 0) or 0),
                    quantity=int(asset.get("quantity", 0) or 0),
                    is_singleton=bool(asset.get("is_singleton", False)),
                    is_blueprint=bool(asset.get("is_blueprint", False)),
                    synced_at=now,
                )
            )

        if rows:
            all_rows.extend(rows)

        done += 1
        cache.set(
            progress_key,
            {
                "running": True,
                "finished": False,
                "error": None,
                "total": total,
                "done": done,
                "failed": failed,
            },
            ttl_seconds,
        )

    cache.set(
        progress_key,
        {
            "running": False,
            "finished": True,
            "error": None,
            "total": total,
            "done": done,
            "failed": failed,
        },
        ttl_seconds,
    )

    # Only replace cached rows if we managed to fetch at least some assets.
    # This prevents the sell page from losing previously cached data when ESI is down
    # or when all characters are missing the required scope.
    if not all_rows:
        cache.set(
            progress_key,
            {
                "running": False,
                "finished": True,
                "error": "no_assets_fetched",
                "total": total,
                "done": done,
                "failed": failed,
            },
            ttl_seconds,
        )
        return

    with transaction.atomic():
        CachedCharacterAsset.objects.filter(user=user).delete()
        CachedCharacterAsset.objects.bulk_create(all_rows, batch_size=1000)

    logger.info(
        "Successfully refreshed %s character assets for user %s",
        len(all_rows),
        user.id,
    )


def _me_buy_stock_refresh_progress_key(corporation_id: int) -> str:
    return f"indy_hub:material_exchange:buy_stock_refresh:{int(corporation_id)}"


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    rate_limit="100/m",
    time_limit=300,
    soft_time_limit=280,
)
def refresh_material_exchange_buy_stock(corporation_id: int) -> None:
    """Refresh corporation assets and update Material Exchange stock for buy page.

    Progress is stored in the Django cache and consumed by the buy page.
    """
    logger.info("Starting buy stock refresh task for corporation %s", corporation_id)

    progress_key = _me_buy_stock_refresh_progress_key(int(corporation_id))
    ttl_seconds = 10 * 60

    try:
        # Fetch fresh corp assets from ESI
        logger.info("Fetching corporation assets from ESI for %s", corporation_id)
        force_refresh_corp_assets(int(corporation_id))

        # Now sync the material exchange stock based on fresh corp assets
        logger.info("Syncing Material Exchange stock from refreshed corp assets")
        _sync_stock_impl()

        cache.set(
            progress_key,
            {
                "running": False,
                "finished": True,
                "error": None,
            },
            ttl_seconds,
        )
        logger.info(
            "Buy stock refresh completed successfully for corporation %s",
            corporation_id,
        )
    except Exception as exc:
        logger.error(
            "Buy stock refresh failed for corporation %s: %s",
            corporation_id,
            exc,
            exc_info=True,
        )
        cache.set(
            progress_key,
            {
                "running": False,
                "finished": True,
                "error": "refresh_failed",
            },
            ttl_seconds,
        )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="100/m",
    time_limit=300,
    soft_time_limit=280,
)
def sync_material_exchange_stock():
    """
    Celery task to sync material stock from ESI corp assets.
    Delegates to the implementation function.
    """
    _sync_stock_impl()


def _sync_stock_impl():
    """
    Implementation of material stock synchronization.
    Can be called from Celery tasks or directly from other async tasks.
    """
    try:
        config = MaterialExchangeConfig.objects.first()
        if not config:
            logger.warning("Material Exchange not configured - skipping stock sync")
            return

        # Filter assets for specific structure and hangar division
        # hangar_division maps to flag: CorpSAG1 = division 1, etc.
        hangar_flag_map = {
            1: "CorpSAG1",
            2: "CorpSAG2",
            3: "CorpSAG3",
            4: "CorpSAG4",
            5: "CorpSAG5",
            6: "CorpSAG6",
            7: "CorpSAG7",
        }
        target_flag = hangar_flag_map.get(config.hangar_division)
        if not target_flag:
            logger.warning(
                "Invalid hangar division %s on config %s; cannot filter assets",
                config.hangar_division,
                config.pk,
            )
            return

        stock_updates: dict[int, int] = {}

        corp_assets, assets_scope_missing = get_corp_assets_cached(
            int(config.corporation_id)
        )
        if assets_scope_missing:
            logger.warning("Missing corp assets scope for %s", config.corporation_id)

        # Upwell structures store corp hangar contents under the OfficeFolder item_id.
        # Stations (and some locations) use the structure/station id directly.
        office_folder_item_id = get_office_folder_item_id_from_assets(
            corp_assets, structure_id=int(config.structure_id)
        )
        effective_location_id = (
            int(office_folder_item_id)
            if office_folder_item_id is not None
            else int(config.structure_id)
        )

        for asset in corp_assets:
            try:
                if int(asset.get("location_id", 0)) != int(effective_location_id):
                    continue
            except (TypeError, ValueError):
                continue

            if asset.get("location_flag") != target_flag:
                continue

            try:
                type_id = int(asset.get("type_id"))
            except (TypeError, ValueError):
                continue

            qty_raw = asset.get("quantity", 1)
            try:
                quantity = int(qty_raw or 0)
            except (TypeError, ValueError):
                quantity = 1
            if quantity <= 0:
                quantity = 1 if asset.get("is_singleton") else 0

            stock_updates[type_id] = stock_updates.get(type_id, 0) + quantity

        logger.info(
            "Loaded %d asset types from cache for structure %s, division %s",
            len(stock_updates),
            config.structure_id,
            config.hangar_division,
        )

        # Update MaterialExchangeStock with atomic transaction
        with transaction.atomic():
            # Desired set of type_ids based on current corp assets
            desired_ids = {int(tid) for tid in stock_updates.keys()}
            now = timezone.now()

            # Current set of type_ids in MaterialExchangeStock for this config
            existing_stocks = MaterialExchangeStock.objects.filter(
                config=config
            ).values_list("type_id", "quantity")
            current_data = {int(tid): int(qty) for tid, qty in existing_stocks}
            current_ids = set(current_data.keys())

            # Delete items that are no longer present
            to_delete = current_ids - desired_ids
            if to_delete:
                deleted_count, _ = MaterialExchangeStock.objects.filter(
                    config=config, type_id__in=list(to_delete)
                ).delete()
                logger.info(
                    "Deleted %d obsolete stock items for config %s",
                    deleted_count,
                    config.pk,
                )

            # If no assets found, ensure table reflects reality (empty)
            if not desired_ids and current_ids:
                deleted_count, _ = MaterialExchangeStock.objects.filter(
                    config=config
                ).delete()
                logger.info(
                    "Cleared all stock items for config %s (no assets in structure)",
                    config.pk,
                )

            # Separate new vs existing items for bulk operations
            to_create = []
            to_update = []

            # Fetch existing items with their PKs for updates
            existing_items = {
                int(item.type_id): item
                for item in MaterialExchangeStock.objects.filter(config=config)
            }

            # Track which items had quantity changes
            items_with_qty_change = set()

            for type_id, quantity in stock_updates.items():
                type_id = int(type_id)
                quantity = int(quantity or 0)
                type_name = get_type_name(type_id)

                if type_id not in current_ids:
                    # New item
                    to_create.append(
                        MaterialExchangeStock(
                            config=config,
                            type_id=type_id,
                            type_name=type_name,
                            quantity=quantity,
                            last_stock_sync=now,
                        )
                    )
                else:
                    existing_item = existing_items[type_id]
                    # Check if quantity or type_name changed
                    if quantity != current_data[type_id]:
                        existing_item.quantity = quantity
                        items_with_qty_change.add(type_id)
                    # Always update type_name in case it changed
                    if existing_item.type_name != type_name:
                        existing_item.type_name = type_name
                    # Always update last_stock_sync and updated_at for all existing items
                    existing_item.last_stock_sync = now
                    existing_item.updated_at = now
                    to_update.append(existing_item)

            # Bulk create new items
            if to_create:
                MaterialExchangeStock.objects.bulk_create(
                    to_create,
                    batch_size=500,
                    ignore_conflicts=False,
                )
                logger.info(
                    "Created %d new stock items for config %s",
                    len(to_create),
                    config.pk,
                )

            # Bulk update existing items (all items get last_stock_sync and updated_at updated)
            if to_update:
                MaterialExchangeStock.objects.bulk_update(
                    to_update,
                    fields=["quantity", "type_name", "last_stock_sync", "updated_at"],
                    batch_size=500,
                )
                logger.info(
                    "Updated %d stock items for config %s (qty changes: %d)",
                    len(to_update),
                    config.pk,
                    len(items_with_qty_change),
                )

            logger.debug(
                "Stock sync summary: created=%d, updated=%d, deleted=%d",
                len(to_create),
                len(to_update),
                len(to_delete),
            )

            config.last_stock_sync = now
            config.save(update_fields=["last_stock_sync"])

        logger.info(
            "Material Exchange stock sync completed: %s types updated",
            len(stock_updates),
        )

        # Auto-sync prices after stock updates so buy page has prices
        try:
            sync_material_exchange_prices()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Auto price sync failed after stock sync: %s", exc)

    except Exception as e:
        logger.exception(f"Error syncing material exchange stock: {e}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="100/m",
    time_limit=60,
    soft_time_limit=50,
)
def sync_material_exchange_prices():
    """
    Sync Jita buy/sell prices from Fuzzwork API for all stock items.
    Updates MaterialExchangeStock jita_buy_price and jita_sell_price.
    """
    try:
        # Third Party
        import requests

        stock_items = MaterialExchangeStock.objects.filter(quantity__gt=0)
        if not stock_items.exists():
            logger.info("No stock items to sync prices for")
            return

        # Collect all type_ids
        type_ids = list(stock_items.values_list("type_id", flat=True))

        # Fuzzwork API supports batch requests
        # https://market.fuzzwork.co.uk/aggregates/?station=60003760&types=34,35,36
        # Jita 4-4 = station_id 60003760
        jita_station_id = 60003760
        type_ids_str = ",".join(map(str, type_ids))

        url = f"https://market.fuzzwork.co.uk/aggregates/?station={jita_station_id}&types={type_ids_str}"

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            prices_data = response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch prices from Fuzzwork: {e}")
            return

        # Update stock prices
        with transaction.atomic():
            for stock_item in stock_items:
                type_id_str = str(stock_item.type_id)
                if type_id_str in prices_data:
                    price_info = prices_data[type_id_str]

                    # Fuzzwork returns buy/sell prices
                    jita_buy = Decimal(str(price_info.get("buy", {}).get("max", 0)))
                    jita_sell = Decimal(str(price_info.get("sell", {}).get("min", 0)))

                    stock_item.jita_buy_price = jita_buy
                    stock_item.jita_sell_price = jita_sell
                    stock_item.save(update_fields=["jita_buy_price", "jita_sell_price"])

                    logger.debug(
                        f"Price sync: {get_type_name(stock_item.type_id)} "
                        f"buy={jita_buy:,.2f} sell={jita_sell:,.2f}"
                    )

            # Update config timestamp
            config = MaterialExchangeConfig.objects.first()
            if config:
                config.last_price_sync = timezone.now()
                config.save(update_fields=["last_price_sync"])

        logger.info(
            f"Material Exchange prices sync completed: {len(type_ids)} types updated"
        )

    except Exception as e:
        logger.exception(f"Error syncing material exchange prices: {e}")
