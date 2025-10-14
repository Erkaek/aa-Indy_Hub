# Tâches asynchrones pour l'industrie (exemple)
# Copie ici les tâches liées à l'industrie extraites de tasks.py
# Place ici les tâches asynchrones spécifiques à l'industrie extraites de tasks.py si besoin

# Standard Library
import logging
from datetime import datetime

# Third Party
from celery import shared_task

# Django
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from ..models import Blueprint, IndustryJob
from ..services.esi_client import ESIClientError, ESITokenError, shared_client
from ..services.location_population import populate_location_names
from ..utils.eve import (
    PLACEHOLDER_PREFIX,
    batch_cache_type_names,
    get_character_name,
    get_type_name,
    resolve_location_name,
)

logger = logging.getLogger(__name__)

BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
STRUCTURE_SCOPE = "esi-universe.read_structures.v1"


def _get_location_lookup_budget() -> int:
    try:
        value = int(getattr(settings, "INDY_HUB_LOCATION_LOOKUP_BUDGET", 50))
    except (TypeError, ValueError):
        value = 50
    return max(value, 0)


def _coerce_job_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            return None
    else:
        return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


@shared_task(bind=True, max_retries=3)
def update_blueprints_for_user(self, user_id):
    required_scopes = [BLUEPRINT_SCOPE, STRUCTURE_SCOPE]
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "Utilisateur %s introuvable pour la synchronisation des plans", user_id
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))

    logger.info("Synchronisation des blueprints pour %s", user.username)
    updated_count = 0
    deleted_total = 0
    error_messages: list[str] = []

    ownerships = CharacterOwnership.objects.filter(user=user)
    for ownership in ownerships:
        char_id = ownership.character.character_id
        character_name = get_character_name(char_id)
        try:
            # Alliance Auth
            from esi.models import Token

            token_exists = (
                Token.objects.filter(character_id=char_id, user=user)
                .require_scopes(required_scopes)
                .exists()
            )
            if not token_exists:
                message = (
                    f"{character_name} ({char_id}) sans jeton pour les scopes "
                    f"{', '.join(required_scopes)}"
                )
                logger.debug(message)
                error_messages.append(message)
                continue

            blueprints = shared_client.fetch_character_blueprints(char_id)
        except ESITokenError as exc:
            message = f"Jeton invalide pour {character_name} ({char_id}): {exc}"
            logger.warning(message)
            error_messages.append(message)
            continue
        except ESIClientError as exc:
            message = f"Erreur ESI pour {character_name} ({char_id}): {exc}"
            logger.error(message)
            error_messages.append(message)
            continue
        except Exception as exc:  # pragma: no cover - unexpected
            message = f"Erreur inattendue pour {character_name} ({char_id}): {exc}"
            logger.exception(message)
            error_messages.append(message)
            continue

        esi_ids = set()
        with transaction.atomic():
            for bp in blueprints:
                item_id = bp.get("item_id")
                esi_ids.add(item_id)
                location_id = bp.get("location_id")
                location_name = resolve_location_name(
                    location_id,
                    character_id=char_id,
                    owner_user_id=user.id,
                )
                Blueprint.objects.update_or_create(
                    owner_user=user,
                    character_id=char_id,
                    item_id=item_id,
                    defaults={
                        "blueprint_id": bp.get("blueprint_id"),
                        "type_id": bp.get("type_id"),
                        "location_id": location_id,
                        "location_name": location_name,
                        "location_flag": bp.get("location_flag", ""),
                        "quantity": bp.get("quantity"),
                        "time_efficiency": bp.get("time_efficiency", 0),
                        "material_efficiency": bp.get("material_efficiency", 0),
                        "runs": bp.get("runs", 0),
                        "character_name": character_name,
                        "type_name": get_type_name(bp.get("type_id")),
                    },
                )

            deleted, _ = (
                Blueprint.objects.filter(owner_user=user, character_id=char_id)
                .exclude(item_id__in=esi_ids)
                .delete()
            )
        deleted_total += deleted
        updated_count += len(blueprints)
        logger.debug(
            "Synchronisation des blueprints terminée pour %s (%s mis à jour, %s supprimés)",
            character_name,
            len(blueprints),
            deleted,
        )

    logger.info(
        "Blueprints synchronisés pour %s: %s éléments mis à jour, %s supprimés",
        user.username,
        updated_count,
        deleted_total,
    )
    if error_messages:
        logger.warning(
            "Incidents lors de la synchronisation des blueprints %s: %s",
            user.username,
            "; ".join(error_messages),
        )

    return {
        "success": True,
        "blueprints_updated": updated_count,
        "deleted": deleted_total,
        "errors": error_messages,
    }


@shared_task(bind=True, max_retries=3)
def update_industry_jobs_for_user(self, user_id):
    try:
        user = User.objects.get(id=user_id)
        logger.info("Starting industry jobs update for user %s", user.username)
        updated_count = 0
        deleted_total = 0
        error_messages: list[str] = []
        location_cache: dict[int, str] = {}
        lookup_budget = _get_location_lookup_budget()
        lookup_budget_warned = False
        ownerships = CharacterOwnership.objects.filter(user=user)
        required_scopes = [JOBS_SCOPE, STRUCTURE_SCOPE]

        for ownership in ownerships:
            char_id = ownership.character.character_id
            character_name = get_character_name(char_id)
            try:
                # Alliance Auth
                from esi.models import Token

                token_exists = (
                    Token.objects.filter(character_id=char_id, user=user)
                    .require_scopes(required_scopes)
                    .exists()
                )
                if not token_exists:
                    message = (
                        f"{character_name} ({char_id}) sans jeton pour les scopes "
                        f"{', '.join(required_scopes)}"
                    )
                    logger.debug(message)
                    error_messages.append(message)
                    continue

                jobs = shared_client.fetch_character_industry_jobs(char_id)
            except ESITokenError as exc:
                message = f"Jeton invalide pour {character_name} ({char_id}): {exc}"
                logger.warning(message)
                error_messages.append(message)
                continue
            except ESIClientError as exc:
                message = f"Erreur ESI pour {character_name} ({char_id}): {exc}"
                logger.error(message)
                error_messages.append(message)
                continue
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Erreur inattendue pour {character_name} ({char_id}): {exc}"
                logger.exception(message)
                error_messages.append(message)
                continue

            esi_job_ids = set()
            with transaction.atomic():
                for job in jobs:
                    job_id = job.get("job_id")
                    esi_job_ids.add(job_id)
                    station_id = job.get("station_id") or job.get("facility_id")
                    location_name = ""
                    if station_id is not None:
                        try:
                            location_key = int(station_id)
                        except (TypeError, ValueError):
                            location_key = None

                        if location_key is not None:
                            cached_name = location_cache.get(location_key)
                            if cached_name is not None:
                                location_name = cached_name
                            elif lookup_budget > 0:
                                try:
                                    resolved_name = resolve_location_name(
                                        location_key,
                                        character_id=char_id,
                                        owner_user_id=user.id,
                                    )
                                except (
                                    Exception
                                ):  # pragma: no cover - defensive fallback
                                    logger.debug(
                                        "Location resolution failed for %s via %s",
                                        location_key,
                                        character_name,
                                        exc_info=True,
                                    )
                                    resolved_name = None

                                lookup_budget -= 1
                                location_name = (
                                    resolved_name
                                    if resolved_name
                                    else f"{PLACEHOLDER_PREFIX}{location_key}"
                                )
                                location_cache[location_key] = location_name
                            else:
                                if not lookup_budget_warned:
                                    logger.warning(
                                        "Location lookup budget exhausted while syncing industry jobs for %s; remaining locations will use placeholders.",
                                        user.username,
                                    )
                                    lookup_budget_warned = True
                                location_name = location_cache.setdefault(
                                    location_key,
                                    f"{PLACEHOLDER_PREFIX}{location_key}",
                                )
                    start_date = _coerce_job_datetime(job.get("start_date"))
                    end_date = _coerce_job_datetime(job.get("end_date"))
                    pause_date = _coerce_job_datetime(job.get("pause_date"))
                    completed_date = _coerce_job_datetime(job.get("completed_date"))

                    if start_date is None:
                        logger.warning(
                            "Skipping job %s for %s due to invalid start date %r",
                            job_id,
                            character_name,
                            job.get("start_date"),
                        )
                        continue

                    if end_date is None:
                        logger.warning(
                            "Job %s for %s missing end date; defaulting to start date.",
                            job_id,
                            character_name,
                        )
                        end_date = start_date

                    IndustryJob.objects.update_or_create(
                        owner_user=user,
                        character_id=char_id,
                        job_id=job_id,
                        defaults={
                            "installer_id": job.get("installer_id"),
                            "station_id": station_id,
                            "location_name": location_name,
                            "activity_id": job.get("activity_id"),
                            "blueprint_id": job.get("blueprint_id"),
                            "blueprint_type_id": job.get("blueprint_type_id"),
                            "runs": job.get("runs"),
                            "cost": job.get("cost"),
                            "licensed_runs": job.get("licensed_runs"),
                            "probability": job.get("probability"),
                            "product_type_id": job.get("product_type_id"),
                            "status": job.get("status"),
                            "duration": job.get("duration"),
                            "start_date": start_date,
                            "end_date": end_date,
                            "pause_date": pause_date,
                            "completed_date": completed_date,
                            "completed_character_id": job.get("completed_character_id"),
                            "successful_runs": job.get("successful_runs"),
                            "blueprint_type_name": get_type_name(
                                job.get("blueprint_type_id")
                            ),
                        },
                    )

                deleted, _ = (
                    IndustryJob.objects.filter(owner_user=user, character_id=char_id)
                    .exclude(job_id__in=esi_job_ids)
                    .delete()
                )

            deleted_total += deleted
            updated_count += len(jobs)
            logger.debug(
                "Synchronisation des jobs terminée pour %s (%s mis à jour, %s supprimés)",
                character_name,
                len(jobs),
                deleted,
            )

        logger.info(
            "Jobs synchronisés pour %s: %s mis à jour, %s supprimés",
            user.username,
            updated_count,
            deleted_total,
        )
        if error_messages:
            logger.warning(
                "Incidents lors de la synchronisation des jobs %s: %s",
                user.username,
                "; ".join(error_messages),
            )
        return {
            "success": True,
            "jobs_updated": updated_count,
            "deleted": deleted_total,
            "errors": error_messages,
        }
    except Exception as e:
        logger.error(f"Error updating jobs for user {user_id}: {e}")
        # Error tracking removed in unified settings
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def cleanup_old_jobs():
    """
    Supprime uniquement les jobs orphelins :
    - jobs dont le owner_user n'existe plus
    - jobs dont le character_id ne correspond à aucun CharacterOwnership
    - jobs dont le token ESI n'existe plus pour ce user/char
    """
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership
    from esi.models import Token

    # Jobs sans user
    jobs_no_user = IndustryJob.objects.filter(owner_user__isnull=True)
    count_no_user = jobs_no_user.count()
    jobs_no_user.delete()

    # Jobs sans character ownership
    jobs = IndustryJob.objects.all()
    char_ids = set(
        CharacterOwnership.objects.values_list("character__character_id", flat=True)
    )
    jobs_no_char = jobs.exclude(character_id__in=char_ids)
    count_no_char = jobs_no_char.count()
    jobs_no_char.delete()

    # Jobs sans token valide (aucun token pour ce user/char)
    deleted_tokenless = 0
    for job in IndustryJob.objects.all():
        has_token = Token.objects.filter(
            user=job.owner_user, character_id=job.character_id
        ).exists()
        if not has_token:
            job.delete()
            deleted_tokenless += 1

    total_deleted = count_no_user + count_no_char + deleted_tokenless
    logger.info(
        f"Cleaned up {total_deleted} orphaned industry jobs (no user: {count_no_user}, no char: {count_no_char}, no token: {deleted_tokenless})"
    )
    return {
        "deleted_jobs": total_deleted,
        "no_user": count_no_user,
        "no_char": count_no_char,
        "no_token": deleted_tokenless,
    }


@shared_task
def update_type_names():
    blueprints_without_names = Blueprint.objects.filter(type_name="")
    type_ids = list(blueprints_without_names.values_list("type_id", flat=True))
    if type_ids:
        batch_cache_type_names(type_ids)
        for bp in blueprints_without_names:
            bp.refresh_from_db()
    jobs_without_names = IndustryJob.objects.filter(blueprint_type_name="")
    job_type_ids = list(jobs_without_names.values_list("blueprint_type_id", flat=True))
    product_type_ids = list(
        jobs_without_names.exclude(product_type_id__isnull=True).values_list(
            "product_type_id", flat=True
        )
    )
    all_type_ids = list(set(job_type_ids + product_type_ids))
    if all_type_ids:
        batch_cache_type_names(all_type_ids)
        for job in jobs_without_names:
            job.refresh_from_db()
    logger.info("Updated type names for blueprints and jobs")


@shared_task(bind=True, max_retries=0)
def populate_location_names_async(
    self, location_ids=None, force_refresh=False, dry_run=False
):
    """Populate location names for blueprints and industry jobs asynchronously."""

    logger.info(
        "Starting async population job for location names%s",
        f" (limited to {len(location_ids)} IDs)" if location_ids else "",
    )

    normalized_ids = None
    if location_ids is not None:
        normalized_ids = [int(value) for value in location_ids if value]
        if not normalized_ids:
            logger.info("No valid location IDs supplied; skipping population job")
            return {"blueprints": 0, "jobs": 0, "locations": 0}

    summary = populate_location_names(
        location_ids=normalized_ids,
        force_refresh=force_refresh,
        dry_run=dry_run,
        schedule_async=not force_refresh,
    )

    logger.info(
        "Location population completed: %s blueprints, %s jobs (%s locations)",
        summary.get("blueprints", 0),
        summary.get("jobs", 0),
        summary.get("locations", 0),
    )
    return summary


@shared_task
def update_all_blueprints():
    """
    Update blueprints for all users - runs every 30 minutes
    """
    logger.info("Starting bulk blueprint update for all users")

    # Get users who have ESI tokens and haven't been updated recently

    # Since we removed tracking, just update all users with tokens
    users_to_update = User.objects.filter(token__isnull=False).distinct()

    for user in users_to_update:
        update_blueprints_for_user.delay(user.id)

    logger.info(f"Queued blueprint updates for {users_to_update.count()} users")
    return {"users_queued": users_to_update.count()}


@shared_task
def update_all_industry_jobs():
    """
    Update industry jobs for all users - runs every 10 minutes
    """
    logger.info("Starting bulk industry jobs update for all users")

    # Get users who have ESI tokens and haven't been updated recently

    # Since we removed tracking, just update all users with tokens
    users_to_update = User.objects.filter(token__isnull=False).distinct()

    for user in users_to_update:
        update_industry_jobs_for_user.delay(user.id)

    logger.info(f"Queued industry job updates for {users_to_update.count()} users")
    return {"users_queued": users_to_update.count()}
