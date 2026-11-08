# Django
from django.db import connection, transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.hooks import get_extension_logger

from .models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    CharacterRoles,
    CharacterSettings,
    CorporationSharingSetting,
    IndustryJob,
    IndyHubUserUsage,
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
)
from .services.admin_user_status import (
    rebuild_admin_user_statuses,
    update_admin_user_status_usage,
)
from .services.material_exchange_sync import enqueue_material_exchange_sync
from .utils.eve import PLACEHOLDER_PREFIX, resolve_location_name
from .utils.job_notifications import process_job_completion_notification

# Alliance Auth: Token model
try:
    # Alliance Auth
    from esi.models import Token
except ImportError:
    Token = None

from .utils.menu_badge import invalidate_menu_badge_cache
from .utils.token_status import invalidate_token_status_cache

logger = get_extension_logger(__name__)


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
        # Never trigger authenticated ESI/token refresh while we are already in
        # an atomic block (e.g. bulk sync upserts). A lock timeout during token
        # refresh would mark the transaction as broken and cascade into
        # TransactionManagementError for the caller.
        in_atomic_block = bool(getattr(connection, "in_atomic_block", False))
        resolved_name = resolve_location_name(
            normalized_id,
            character_id=character_id,
            owner_user_id=owner_user_id,
            allow_public=not in_atomic_block,
            allow_authenticated=not in_atomic_block,
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
    Reserved for future post-save blueprint cache hooks.

    Location-name resolution is handled during pre-save through the shared
    cache-aware resolver.
    """
    pass


@receiver(post_save, sender=IndustryJob)
def cache_industry_job_data(sender, instance, created, **kwargs):
    process_job_completion_notification(instance)


def _invalidate_blueprint_copy_badges(instance) -> None:
    request_obj = None

    if isinstance(instance, BlueprintCopyRequest):
        request_obj = instance
    elif isinstance(instance, BlueprintCopyOffer):
        request_obj = instance.request
    elif isinstance(instance, BlueprintCopyChat):
        request_obj = instance.request

    if not request_obj:
        return

    invalidate_menu_badge_cache(
        getattr(request_obj, "requested_by_id", None),
        getattr(getattr(instance, "owner", None), "id", None),
        getattr(instance, "owner_id", None),
        getattr(instance, "buyer_id", None),
        getattr(instance, "seller_id", None),
    )


@receiver(post_save, sender=BlueprintCopyRequest)
@receiver(post_delete, sender=BlueprintCopyRequest)
@receiver(post_save, sender=BlueprintCopyOffer)
@receiver(post_delete, sender=BlueprintCopyOffer)
@receiver(post_save, sender=BlueprintCopyChat)
@receiver(post_delete, sender=BlueprintCopyChat)
def invalidate_blueprint_copy_menu_badges(sender, instance, **kwargs):
    _invalidate_blueprint_copy_badges(instance)


# --- Invalidate menu badge whenever an ESI token is added/removed/refreshed ---

if Token is not None:

    @receiver(post_save, sender=Token)
    @receiver(post_delete, sender=Token)
    def _invalidate_menu_badge_on_token_change(sender, instance, **kwargs):
        """Refresh the Indy Hub navbar badge as soon as a user (re)links a character.

        Without this, the AA-side badge would keep its cached value even
        after the user re-authorizes the missing scopes, which is misleading.
        """
        _invalidate_token_views(
            getattr(instance, "user_id", None), using=kwargs.get("using", "default")
        )


@receiver(post_save, sender=CorporationSharingSetting)
@receiver(post_delete, sender=CorporationSharingSetting)
def invalidate_corporation_token_status(sender, instance, **kwargs):
    invalidate_token_status_cache(instance.user_id)


def _invalidate_token_views(*user_ids, using="default"):
    def invalidate():
        invalidate_menu_badge_cache(*user_ids)
        invalidate_token_status_cache(*user_ids)

    invalidate()
    # Another request can refill a cache before this transaction commits.
    transaction.on_commit(invalidate, using=using)


@receiver(post_save, sender=CharacterRoles)
@receiver(post_delete, sender=CharacterRoles)
def invalidate_role_token_status(sender, instance, **kwargs):
    _invalidate_token_views(
        instance.owner_user_id, using=kwargs.get("using", "default")
    )


def _refresh_admin_user_status_safely(*user_ids) -> None:
    normalized_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    if not normalized_ids:
        return
    try:
        rebuild_admin_user_statuses(normalized_ids)
    except Exception:
        logger.warning(
            "Unable to refresh admin-user read model for users %s",
            normalized_ids,
            exc_info=True,
        )


@receiver(post_save, sender=CharacterSettings)
@receiver(post_delete, sender=CharacterSettings)
def refresh_admin_user_status_on_settings_change(sender, instance, **kwargs):
    if int(instance.character_id or 0) == 0:
        _refresh_admin_user_status_safely(instance.user_id)


@receiver(post_save, sender=IndyHubUserUsage)
def refresh_admin_user_status_on_usage_save(sender, instance, **kwargs):
    try:
        update_admin_user_status_usage(instance)
    except Exception:
        logger.warning(
            "Unable to refresh admin-user usage status for user %s",
            instance.user_id,
            exc_info=True,
        )


@receiver(post_delete, sender=IndyHubUserUsage)
def refresh_admin_user_status_on_usage_delete(sender, instance, **kwargs):
    _refresh_admin_user_status_safely(instance.user_id)


@receiver(post_save, sender=UserProfile)
@receiver(post_delete, sender=UserProfile)
def refresh_admin_user_status_on_profile_change(sender, instance, **kwargs):
    _refresh_admin_user_status_safely(instance.user_id)


@receiver(post_save, sender=EveCharacter)
def refresh_admin_user_status_on_character_change(sender, instance, **kwargs):
    user_ids = UserProfile.objects.filter(main_character=instance).values_list(
        "user_id", flat=True
    )
    _refresh_admin_user_status_safely(*user_ids)


if Token is not None:

    @receiver(post_save, sender=Token)
    @receiver(post_delete, sender=Token)
    def refresh_admin_user_status_on_token_change(sender, instance, **kwargs):
        _refresh_admin_user_status_safely(getattr(instance, "user_id", None))

    @receiver(m2m_changed, sender=Token.scopes.through)
    def refresh_admin_user_status_on_token_scopes_change(
        sender,
        instance,
        action,
        reverse,
        pk_set,
        **kwargs,
    ):
        if reverse and action == "pre_clear":
            token_user_ids = Token.objects.filter(scopes=instance).values_list(
                "user_id", flat=True
            )
            setattr(
                instance,
                "_indy_hub_admin_status_user_ids",
                [int(user_id) for user_id in token_user_ids if user_id],
            )
            return
        if action not in {"post_add", "post_remove", "post_clear"}:
            return
        if not reverse:
            _invalidate_token_views(
                getattr(instance, "user_id", None), using=kwargs.get("using", "default")
            )
            _refresh_admin_user_status_safely(getattr(instance, "user_id", None))
            return
        if action == "post_clear":
            token_user_ids = getattr(
                instance,
                "_indy_hub_admin_status_user_ids",
                [],
            )
            if hasattr(instance, "_indy_hub_admin_status_user_ids"):
                delattr(instance, "_indy_hub_admin_status_user_ids")
        else:
            token_user_ids = Token.objects.filter(pk__in=pk_set or []).values_list(
                "user_id", flat=True
            )
        token_user_ids = list(token_user_ids)
        _invalidate_token_views(*token_user_ids, using=kwargs.get("using", "default"))
        _refresh_admin_user_status_safely(*token_user_ids)


# --- Auto stock/price sync when MaterialExchangeConfig changes ---


@receiver(pre_save, sender=MaterialExchangeConfig)
def detect_config_change(sender, instance, **kwargs):
    """Flag the instance when key fields change so we can act post-commit."""
    instance._needs_exchange_sync = False
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
    queue stock (and its price follow-up) after the configuration commits.
    """
    needs_sync = created or getattr(instance, "_needs_exchange_sync", False)
    instance._needs_exchange_sync = False

    logger.info(
        "MaterialExchangeConfig post_save: created=%s, needs_sync=%s, pk=%s",
        created,
        needs_sync,
        instance.pk,
    )

    if not needs_sync:
        logger.debug("No sync needed for MaterialExchangeConfig pk=%s", instance.pk)
        return

    transaction.on_commit(enqueue_material_exchange_sync, using=kwargs.get("using"))


# Scope changes are hints, never a global OAuth-finalized event.
if Token is not None:

    @receiver(pre_save, sender=Token)
    def remember_token_owner(sender, instance, **kwargs):
        instance._indy_hub_previous_user_id = _get_previous_field_value(
            Token, instance.pk, "user_id"
        )

    @receiver(post_save, sender=Token)
    def trigger_sync_on_token_save(
        sender, instance, created, using="default", **kwargs
    ):
        previous_user_id = getattr(
            instance, "_indy_hub_previous_user_id", instance.user_id
        )
        if not created and previous_user_id != instance.user_id:
            from .services.token_sync import schedule_token_sync

            _invalidate_token_views(previous_user_id, using=using)
            _refresh_admin_user_status_safely(previous_user_id)
            schedule_token_sync(instance.user_id, instance.character_id, using=using)

    @receiver(m2m_changed, sender=Token.scopes.through)
    def trigger_sync_on_token_scopes(
        sender, instance, action, reverse, pk_set, using="default", **kwargs
    ):
        if action != "post_add" or not pk_set:
            return
        from .services.token_sync import schedule_token_sync

        if reverse:
            scope_names = {instance.name}
            tokens = Token.objects.filter(pk__in=pk_set)
        else:
            scope_names = set(
                instance.scopes.filter(pk__in=pk_set).values_list("name", flat=True)
            )
            tokens = [instance]
        for token in tokens:
            schedule_token_sync(
                token.user_id, token.character_id, scopes=scope_names, using=using
            )


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


def _invalidate_material_exchange_menu_badge(instance) -> None:
    invalidate_menu_badge_cache(
        getattr(instance, "buyer_id", None), getattr(instance, "seller_id", None)
    )


@receiver(post_save, sender=MaterialExchangeBuyOrder)
@receiver(post_delete, sender=MaterialExchangeBuyOrder)
@receiver(post_save, sender=MaterialExchangeSellOrder)
@receiver(post_delete, sender=MaterialExchangeSellOrder)
def invalidate_material_exchange_menu_badge(sender, instance, **kwargs):
    _invalidate_material_exchange_menu_badge(instance)
