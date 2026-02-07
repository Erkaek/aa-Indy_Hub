# Django
from django.core.cache import cache
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

from .models import (
    Blueprint,
    IndustryJob,
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
)
from .tasks.material_exchange import (
    sync_material_exchange_prices,
    sync_material_exchange_stock,
)
from .utils.eve import PLACEHOLDER_PREFIX, resolve_location_name
from .utils.job_notifications import process_job_completion_notification

# Alliance Auth: Token model
try:
    # Alliance Auth
    from esi.models import Token
except ImportError:
    Token = None

# AA Example App
# Task imports
from indy_hub.tasks.industry import (
    BLUEPRINT_SCOPE,
    CORP_ASSETS_SCOPE,
    CORP_BLUEPRINT_SCOPE,
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_JOBS_SCOPE,
    CORP_JOBS_SCOPE_SET,
    CORP_STRUCTURES_SCOPE,
    JOBS_SCOPE,
    REQUIRED_CORPORATION_ROLES,
    STRUCTURE_SCOPE,
    get_character_corporation_roles,
    update_blueprints_for_user,
    update_industry_jobs_for_user,
)

from .services.esi_client import ESITokenError

logger = get_extension_logger(__name__)

_TOKEN_SCOPE_GROUPS = {
    "blueprints": {
        "scopes": [BLUEPRINT_SCOPE, STRUCTURE_SCOPE],
        "label": "Blueprints",
    },
    "jobs": {
        "scopes": [JOBS_SCOPE, STRUCTURE_SCOPE],
        "label": "Industry jobs",
    },
    "assets": {
        "scopes": ["esi-assets.read_assets.v1"],
        "label": "Character assets",
    },
    "corp_blueprints": {
        "scopes": list(CORP_BLUEPRINT_SCOPE_SET),
        "label": "Corporation blueprints",
    },
    "corp_jobs": {
        "scopes": list(CORP_JOBS_SCOPE_SET),
        "label": "Corporation industry jobs",
    },
    "corp_assets": {
        "scopes": [CORP_ASSETS_SCOPE],
        "label": "Corporation assets",
    },
    "corp_structures": {
        "scopes": [CORP_STRUCTURES_SCOPE],
        "label": "Corporation structures",
    },
    "material_exchange": {
        "scopes": [
            "esi-assets.read_corporation_assets.v1",
            "esi-corporations.read_divisions.v1",
            "esi-contracts.read_corporation_contracts.v1",
            "esi-universe.read_structures.v1",
        ],
        "label": "Material Exchange",
    },
}


def _token_scope_cache_key(user_id: int, group: str) -> str:
    return f"indy_hub:token_scope_ok:{int(user_id)}:{group}"


def _has_valid_token_for_scopes(user, scopes: list[str]) -> bool:
    if not user or not Token:
        return False
    try:
        return (
            Token.objects.filter(user=user)
            .require_scopes(scopes)
            .require_valid()
            .exists()
        )
    except Exception:
        return False


def _update_scope_status_cache(user) -> None:
    if not user:
        return
    for group, meta in _TOKEN_SCOPE_GROUPS.items():
        ok = _has_valid_token_for_scopes(user, meta["scopes"])
        cache.set(_token_scope_cache_key(user.id, group), ok, 3600)


def _check_scope_loss_and_notify(user) -> None:
    if not user:
        return

    for group, meta in _TOKEN_SCOPE_GROUPS.items():
        cache_key = _token_scope_cache_key(user.id, group)
        previous_ok = cache.get(cache_key)
        current_ok = _has_valid_token_for_scopes(user, meta["scopes"])
        cache.set(cache_key, current_ok, 3600)

        if previous_ok is True and not current_ok:
            try:
                # AA Example App
                from indy_hub.notifications import notify_user

                notify_user(
                    user,
                    "ESI token missing",
                    (
                        f"Your {meta['label']} ESI token is missing. "
                        "Please add a character in the ESI tab."
                    ),
                    level="warning",
                    link=reverse("indy_hub:esi_hub"),
                    link_label="Open ESI",
                )
            except Exception:
                logger.warning(
                    "Failed to notify user %s about missing %s scopes",
                    user,
                    meta["label"],
                    exc_info=True,
                )


def _normalize_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_previous_field_value(model, pk, field_name):
    if not pk:
        return None
    try:
        return model.objects.filter(pk=pk).values_list(field_name, flat=True).first()
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to load previous value for %s.%s (pk=%s)",
            model.__name__,
            field_name,
            pk,
            exc_info=True,
        )
        return None


def _ensure_location_name(instance, *, id_field: str, name_field: str) -> None:
    location_id = getattr(instance, id_field, None)
    current_name = getattr(instance, name_field, "") or ""

    normalized_id = _normalize_int(location_id)

    if normalized_id is None:
        if current_name:
            setattr(instance, name_field, "")
        return

    should_refresh = False

    if not current_name or current_name.startswith(PLACEHOLDER_PREFIX):
        should_refresh = True

    previous_id = _normalize_int(
        _get_previous_field_value(
            instance.__class__, getattr(instance, "pk", None), id_field
        )
    )

    if previous_id is not None and previous_id != normalized_id:
        should_refresh = True

    if not should_refresh:
        return

    owner_user_id = getattr(instance, "owner_user_id", None)
    character_id = getattr(instance, "character_id", None)

    try:
        resolved_name = resolve_location_name(
            normalized_id,
            character_id=character_id,
            owner_user_id=owner_user_id,
        )
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to resolve location name for %s via signal",
            normalized_id,
            exc_info=True,
        )
        resolved_name = None

    if not resolved_name:
        resolved_name = f"{PLACEHOLDER_PREFIX}{normalized_id}"

    setattr(instance, name_field, resolved_name)


@receiver(pre_save, sender=Blueprint)
def sync_blueprint_location_name(sender, instance, **kwargs):
    _ensure_location_name(instance, id_field="location_id", name_field="location_name")


@receiver(pre_save, sender=IndustryJob)
def sync_industry_job_location_name(sender, instance, **kwargs):
    _ensure_location_name(instance, id_field="station_id", name_field="location_name")


@receiver(post_save, sender=Blueprint)
def cache_blueprint_data(sender, instance, created, **kwargs):
    """
    No longer needed: ESI name caching is removed. All lookups are local DB only.
    """
    pass


@receiver(post_save, sender=IndustryJob)
def cache_industry_job_data(sender, instance, created, **kwargs):
    process_job_completion_notification(instance)


# --- Auto stock/price sync when MaterialExchangeConfig changes ---


@receiver(pre_save, sender=MaterialExchangeConfig)
def detect_config_change(sender, instance, **kwargs):
    """Flag the instance when key fields change so we can act post-commit."""
    pk = getattr(instance, "pk", None)
    logger.info(
        "MaterialExchangeConfig pre_save: pk=%s, structure_id=%s, corporation_id=%s, hangar_division=%s",
        pk,
        instance.structure_id,
        instance.corporation_id,
        instance.hangar_division,
    )

    try:
        if not pk:
            # New instance, treat as changed
            logger.info("MaterialExchangeConfig is new, flagging for stock sync")
            setattr(instance, "_needs_exchange_sync", True)
            return

        prev_structure = _get_previous_field_value(sender, pk, "structure_id")
        prev_corporation = _get_previous_field_value(sender, pk, "corporation_id")
        prev_division = _get_previous_field_value(sender, pk, "hangar_division")

        logger.debug(
            "Previous values: structure=%s, corporation=%s, division=%s",
            prev_structure,
            prev_corporation,
            prev_division,
        )

        changed = False
        try:
            if prev_structure is not None and int(prev_structure) != int(
                instance.structure_id
            ):
                logger.info(
                    "MaterialExchangeConfig structure_id changed: %s → %s",
                    prev_structure,
                    instance.structure_id,
                )
                changed = True
        except Exception as e:
            logger.debug("Exception comparing structure_id: %s", e)
            changed = True
        try:
            if prev_corporation is not None and int(prev_corporation) != int(
                instance.corporation_id
            ):
                logger.info(
                    "MaterialExchangeConfig corporation_id changed: %s → %s",
                    prev_corporation,
                    instance.corporation_id,
                )
                changed = True
        except Exception as e:
            logger.debug("Exception comparing corporation_id: %s", e)
            changed = True
        try:
            if prev_division is not None and int(prev_division) != int(
                instance.hangar_division
            ):
                logger.info(
                    "MaterialExchangeConfig hangar_division changed: %s → %s",
                    prev_division,
                    instance.hangar_division,
                )
                changed = True
        except Exception as e:
            logger.debug("Exception comparing hangar_division: %s", e)
            changed = True

        if changed:
            logger.info("Setting _needs_exchange_sync flag on MaterialExchangeConfig")
            setattr(instance, "_needs_exchange_sync", True)
        else:
            logger.debug("No key field changes detected in MaterialExchangeConfig")
    except Exception:
        # Be defensive; if detection fails, don't crash saves
        logger.exception("Exception in detect_config_change, flagging sync anyway")
        setattr(instance, "_needs_exchange_sync", True)


@receiver(post_save, sender=MaterialExchangeConfig)
def auto_sync_stock_on_config_change(sender, instance, created, **kwargs):
    """
    When Material Exchange configuration is created or key fields change,
    immediately refresh stock (and prices) so pages reflect the new structure.
    """
    needs_sync = created or getattr(instance, "_needs_exchange_sync", False)

    logger.info(
        "MaterialExchangeConfig post_save: created=%s, needs_sync=%s, pk=%s",
        created,
        needs_sync,
        instance.pk,
    )

    if not needs_sync:
        logger.debug("No sync needed for MaterialExchangeConfig pk=%s", instance.pk)
        return

    logger.info("SYNCING MATERIAL EXCHANGE STOCK/PRICES for config pk=%s", instance.pk)

    try:
        sync_material_exchange_stock()
        logger.info("Stock sync completed for config pk=%s", instance.pk)
    except Exception:
        logger.exception("Stock sync failed for config pk=%s", instance.pk)

    try:
        sync_material_exchange_prices()
        logger.info("Price sync completed for config pk=%s", instance.pk)
    except Exception:
        logger.exception("Price sync failed for config pk=%s", instance.pk)


@receiver(post_migrate)
def setup_indyhub_periodic_tasks(sender, **kwargs):
    # Only run for the indy_hub app
    if getattr(sender, "name", None) != "indy_hub":
        return
    try:
        # AA Example App
        from indy_hub.tasks import setup_periodic_tasks

        setup_periodic_tasks()
    except Exception as e:
        logger.warning(f"Could not setup indy_hub periodic tasks after migrate: {e}")


# --- NEW: Combined token sync trigger ---
if Token:

    @receiver(post_save, sender=Token)
    def enforce_corporation_role_tokens(sender, instance, created, **kwargs):
        if not created:
            return

        scope_names = set(instance.scopes.values_list("name", flat=True))
        relevant_scopes = {CORP_BLUEPRINT_SCOPE, CORP_JOBS_SCOPE}
        if not scope_names.intersection(relevant_scopes):
            return

        try:
            roles = get_character_corporation_roles(instance.character_id)
        except ESITokenError:
            logger.info(
                "Removing corporation token %s for character %s: missing roles scope",
                instance.pk,
                instance.character_id,
                extra={"scopes": sorted(scope_names)},
            )
            instance.delete()
            return

        if roles.intersection(REQUIRED_CORPORATION_ROLES):
            return

        logger.info(
            "Removing corporation token %s for character %s: lacks required roles %s",
            instance.pk,
            instance.character_id,
            ", ".join(sorted(REQUIRED_CORPORATION_ROLES)),
            extra={"scopes": sorted(scope_names)},
        )
        instance.delete()

    @receiver(post_save, sender=Token)
    def trigger_sync_on_token_save(sender, instance, created, **kwargs):
        """
        When a new ESI token is saved, trigger appropriate sync based on scopes.
        """
        if not instance.user_id:
            logger.debug(f"Token {instance.pk} has no user_id, skipping sync")
            return

        # Only trigger sync for newly created tokens or significant updates
        if not created:
            logger.debug(f"Token {instance.pk} updated but not created, skipping sync")
            return

        logger.info(
            f"New token created for user {instance.user_id}, character {instance.character_id}"
        )

        # Check blueprint scope
        blueprint_scopes = instance.scopes.filter(
            name="esi-characters.read_blueprints.v1"
        )
        if blueprint_scopes.exists():
            logger.info(f"Triggering blueprint sync for user {instance.user_id}")
            try:
                update_blueprints_for_user.delay(instance.user_id)
            except Exception as e:
                logger.error(f"Failed to trigger blueprint sync: {e}")

        # Check jobs scope
        jobs_scopes = instance.scopes.filter(name="esi-industry.read_character_jobs.v1")
        if jobs_scopes.exists():
            logger.info(f"Triggering jobs sync for user {instance.user_id}")
            try:
                update_industry_jobs_for_user.delay(instance.user_id)
            except Exception as e:
                logger.error(f"Failed to trigger jobs sync: {e}")


@receiver(post_save, sender=Token)
def remove_duplicate_tokens(sender, instance, created, **kwargs):
    # After saving a new token, delete any older duplicates for the same character and scopes
    tokens = Token.objects.filter(
        user=instance.user,
        character_id=instance.character_id,
    ).exclude(pk=instance.pk)
    # Compare exact scope sets to identify duplicates
    instance_scope_ids = set(instance.scopes.values_list("id", flat=True))
    for token in tokens:
        if set(token.scopes.values_list("id", flat=True)) == instance_scope_ids:
            token.delete()


@receiver(post_save, sender=Token)
def cache_scope_status_on_token_save(sender, instance, **kwargs):
    if not instance.user_id:
        return
    _check_scope_loss_and_notify(instance.user)


@receiver(post_delete, sender=Token)
def notify_on_token_scope_loss(sender, instance, **kwargs):
    if not instance.user_id:
        return
    _check_scope_loss_and_notify(instance.user)


@receiver(post_save, sender=MaterialExchangeBuyOrder)
def notify_admins_on_buy_order_created(sender, instance, created, **kwargs):
    """
    When a buy order is created, queue notification with batching.
    Multiple orders created close together are consolidated into one task.
    """
    if not created:
        return

    try:
        # Use apply_async with countdown to batch orders created within 2 seconds
        # This reduces queue overhead: 10 orders → 1 batch task instead of 10 tasks
        # AA Example App
        from indy_hub.tasks.material_exchange_contracts import (
            handle_material_exchange_buy_order_created,
        )

        handle_material_exchange_buy_order_created.apply_async(
            args=(instance.id,),
            countdown=2,  # Wait 2 seconds to batch with other orders
            # Keep an expiry to avoid very late processing, but allow for queue lag / clock skew.
            expires=300,
        )
    except Exception as exc:
        logger.error(
            "Failed to queue buy order notification for order %s: %s",
            instance.id,
            exc,
            exc_info=True,
        )
