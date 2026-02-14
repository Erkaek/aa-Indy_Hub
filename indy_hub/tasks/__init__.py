"""Celery tasks package for indy_hub.

Celery's Django autodiscovery imports the app's ``tasks`` module (or package).
Because this is a package with multiple submodules, we need to import those
submodules so their ``@shared_task`` decorators are registered.

However, importing task submodules too early (before Django has initialized the
app registry) can raise errors during test discovery or other contexts.
"""


def _import_task_submodules() -> None:
    """Import task submodules when Django is ready.

    This keeps imports safe during environments where Django isn't initialized
    yet (e.g. module discovery), while still registering Celery tasks when
    running under a fully configured Django/Celery process.
    """

    # Django
    from django.apps import apps

    # During AppConfig.ready(), Django has loaded apps and models, but the global
    # registry flag `apps.ready` is only set to True after *all* apps have run
    # their ready() methods. We only require apps + models to be ready here.
    if not (apps.apps_ready and apps.models_ready):
        return

    # Import task submodules so their @shared_task are registered
    from . import housekeeping  # noqa: F401
    from . import industry  # noqa: F401
    from . import location  # noqa: F401
    from . import material_exchange  # noqa: F401
    from . import material_exchange_contracts  # noqa: F401
    from . import notifications  # noqa: F401
    from . import user  # noqa: F401


def ensure_task_submodules_imported() -> None:
    """Ensure all indy_hub task submodules are imported.

    This is safe to call multiple times and is intended to be called from
    AppConfig.ready() to handle cases where ``indy_hub.tasks`` was imported
    before Django finished initializing.
    """

    _import_task_submodules()


try:
    ensure_task_submodules_imported()
except Exception:
    # Keep this package importable even if Django isn't initialized yet.
    # Submodules will be imported later when Django is ready.
    pass


# Import the setup function from the main tasks module
def setup_periodic_tasks():
    """Setup periodic tasks for IndyHub module."""
    # Standard Library
    import json

    # Alliance Auth
    from allianceauth.services.hooks import get_extension_logger

    logger = get_extension_logger(__name__)

    try:
        # Third Party
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        # AA Example App
        from indy_hub.models import MaterialExchangeSettings
        from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
    except ImportError:
        return  # django_celery_beat is not installed

    created = 0
    updated = 0
    unchanged = 0

    for name, conf in INDY_HUB_BEAT_SCHEDULE.items():
        schedule = conf["schedule"]
        apply_offset = bool(conf.get("apply_offset"))
        if apply_offset:
            try:
                # Alliance Auth
                from allianceauth.crontab.utils import offset_cron

                schedule = offset_cron(schedule)
            except Exception:
                # If offset_cron is unavailable, fall back to original schedule.
                schedule = conf["schedule"]

        def _normalize_cron_value(value, *, field: str) -> str:
            if not isinstance(value, (set, list, tuple)):
                return str(value)

            values = sorted({int(item) for item in value})
            if not values:
                return "*"

            if field == "minute":
                full_range = list(range(0, 60))
            elif field == "hour":
                full_range = list(range(0, 24))
            elif field == "day_of_week":
                full_range = list(range(0, 7))
            elif field == "month_of_year":
                full_range = list(range(1, 13))
            elif field == "day_of_month":
                full_range = list(range(1, 32))
            else:
                full_range = None

            if full_range is not None and values == full_range:
                return "*"

            if len(values) > 1:
                step = values[1] - values[0]
                if step > 0 and all(
                    values[i] - values[i - 1] == step for i in range(1, len(values))
                ):
                    if values[0] == 0:
                        return f"*/{step}"
                    return f"{values[0]}-{values[-1]}/{step}"

            return ",".join(str(item) for item in values)

        def _cron_value(field: str) -> str:
            original = getattr(schedule, f"_orig_{field}", None)
            if original is not None and not apply_offset:
                return _normalize_cron_value(original, field=field)
            return _normalize_cron_value(getattr(schedule, field), field=field)

        crontabs = CrontabSchedule.objects.filter(
            minute=_cron_value("minute"),
            hour=_cron_value("hour"),
            day_of_week=_cron_value("day_of_week"),
            day_of_month=_cron_value("day_of_month"),
            month_of_year=_cron_value("month_of_year"),
        )
        if crontabs.exists():
            crontab = crontabs.first()
        else:
            crontab = CrontabSchedule.objects.create(
                minute=_cron_value("minute"),
                hour=_cron_value("hour"),
                day_of_week=_cron_value("day_of_week"),
                day_of_month=_cron_value("day_of_month"),
                month_of_year=_cron_value("month_of_year"),
            )
        enabled = True
        if name == "indy-hub-material-exchange-cycle":
            try:
                enabled = MaterialExchangeSettings.get_solo().is_enabled
            except Exception:
                enabled = True

        args_json = json.dumps([])
        desired_task = conf["task"]

        existing = (
            PeriodicTask.objects.select_related("crontab")
            .only("id", "name", "task", "crontab_id", "interval_id", "args", "enabled")
            .filter(name=name)
            .first()
        )

        if existing is None:
            PeriodicTask.objects.create(
                name=name,
                task=desired_task,
                crontab=crontab,
                interval=None,
                args=args_json,
                enabled=enabled,
            )
            created += 1
            continue

        needs_update = False
        if existing.task != desired_task:
            existing.task = desired_task
            needs_update = True
        if existing.crontab_id != crontab.id:
            existing.crontab = crontab
            needs_update = True
        if existing.interval_id is not None:
            existing.interval = None
            needs_update = True
        if existing.args != args_json:
            existing.args = args_json
            needs_update = True
        if existing.enabled != enabled:
            existing.enabled = enabled
            needs_update = True

        if needs_update:
            existing.save()
            updated += 1
        else:
            unchanged += 1

    # Clean up any legacy task entries that are no longer defined
    legacy_task_names = [
        "indy-hub-notify-completed-jobs",
        "indy-hub-check-completed-contracts",
        "indy-hub-validate-sell-orders",
        "indy-hub-update-system-cost-indices",
        "indy-hub-refresh-production-items",
        "indy-hub-update-character-roles",
        "indy-hub-update-skill-snapshots",
    ]
    removed_legacy, _ = PeriodicTask.objects.filter(name__in=legacy_task_names).delete()
    if removed_legacy:
        logger.info("Removed %s legacy IndyHub periodic tasks", removed_legacy)

    # Remove any stale IndyHub tasks not present in the current schedule.
    valid_names = set(INDY_HUB_BEAT_SCHEDULE.keys())
    stale_qs = PeriodicTask.objects.filter(name__startswith="indy-hub-").exclude(
        name__in=valid_names
    )
    stale_removed, _ = stale_qs.delete()
    if stale_removed:
        logger.info("Removed %s stale IndyHub periodic tasks", stale_removed)

    removed_total = removed_legacy + stale_removed
    if created or updated or removed_total:
        logger.info(
            "IndyHub periodic tasks updated (created=%s, updated=%s, removed=%s).",
            created,
            updated,
            removed_total,
        )

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed_legacy": removed_legacy,
        "removed_stale": stale_removed,
    }


def remove_periodic_tasks() -> None:
    """Remove IndyHub periodic tasks from django-celery-beat."""
    # Alliance Auth
    from allianceauth.services.hooks import get_extension_logger

    logger = get_extension_logger(__name__)

    try:
        # Third Party
        from django_celery_beat.models import PeriodicTask

        # AA Example App
        from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
    except ImportError:
        return  # django_celery_beat is not installed

    task_names = list(INDY_HUB_BEAT_SCHEDULE.keys()) + [
        "indy-hub-notify-completed-jobs",
        "indy-hub-check-completed-contracts",
        "indy-hub-validate-sell-orders",
        "indy-hub-update-system-cost-indices",
        "indy-hub-refresh-production-items",
        "indy-hub-update-character-roles",
        "indy-hub-update-skill-snapshots",
    ]
    removed, _ = PeriodicTask.objects.filter(name__in=task_names).delete()
    if removed:
        logger.info("Removed %s IndyHub periodic tasks.", removed)


# ...import additional tasks here if needed...
