"""Capability-level token triggers; django-esi owns token finalization.

M2M additions are hints, including in autocommit. Neither a callback nor a
message carries a Token PK: consumers select current scoped authorization.
"""

# Standard Library
import logging
from uuid import uuid4

# Django
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from esi.models import Token

logger = logging.getLogger(__name__)


def schedule_token_sync(user_id, character_id, *, scopes=None, using="default"):
    if not user_id or not character_id:
        return
    transaction.on_commit(
        lambda: _publish_ready_capabilities(
            int(user_id), int(character_id), scopes=scopes
        ),
        using=using,
    )


def _publish_ready_capabilities(user_id, character_id, *, scopes=None):
    # Lazy imports: tasks import signals during app initialization.
    from ..tasks.industry import (
        BLUEPRINT_SCOPE,
        CORP_BLUEPRINT_SCOPE_SET,
        CORP_JOBS_SCOPE_SET,
        JOBS_SCOPE,
        SKILLS_SCOPE,
        STRUCTURE_SCOPE,
        _is_user_active,
        queue_blueprint_update_for_user,
        queue_industry_job_update_for_user,
        update_character_skill_snapshot_for_character,
    )
    from ..tasks.user import CORP_ROLES_SCOPE, update_character_roles_for_character

    try:
        user = get_user_model().objects.filter(pk=user_id).first()
        if (
            not user
            or not user.has_perm("indy_hub.can_access_indy_hub")
            or not _is_user_active(user)
            or not CharacterOwnership.objects.filter(
                user_id=user_id, character__character_id=character_id
            ).exists()
        ):
            return
        tokens = Token.objects.filter(user_id=user_id, character_id=character_id)
        # These consumers need one scope each. Optional structure scopes and
        # other profiles must not delay a capability that is already usable.
        publishers = [
            (
                "blueprints",
                [BLUEPRINT_SCOPE],
                lambda: queue_blueprint_update_for_user(
                    user_id, scope="character", character_id=character_id
                ),
            ),
            (
                "jobs",
                [JOBS_SCOPE],
                lambda: queue_industry_job_update_for_user(
                    user_id, scope="character", character_id=character_id
                ),
            ),
            (
                "skills",
                [SKILLS_SCOPE],
                lambda: update_character_skill_snapshot_for_character.apply_async(
                    args=(user_id, character_id), retry=False
                ),
            ),
            (
                "roles",
                [CORP_ROLES_SCOPE],
                lambda: update_character_roles_for_character.apply_async(
                    args=(user_id, character_id),
                    kwargs={"force_refresh": True},
                    retry=False,
                ),
            ),
        ]
        if user.has_perm("indy_hub.can_manage_corp_bp_requests"):
            # Match the existing consumer's optional structure-name fallback;
            # do not require the union of unrelated corporation profiles.
            publishers.extend(
                [
                    (
                        "corp_blueprints",
                        [s for s in CORP_BLUEPRINT_SCOPE_SET if s != STRUCTURE_SCOPE],
                        lambda: queue_blueprint_update_for_user(
                            user_id, scope="corporation"
                        ),
                    ),
                    (
                        "corp_jobs",
                        [s for s in CORP_JOBS_SCOPE_SET if s != STRUCTURE_SCOPE],
                        lambda: queue_industry_job_update_for_user(
                            user_id, scope="corporation"
                        ),
                    ),
                ]
            )
        for capability, required_scopes, publish in publishers:
            if scopes is not None and not set(required_scopes).intersection(scopes):
                continue
            if not tokens.require_scopes(required_scopes).exists():
                continue
            # Coalesce successive M2M events/commits, also for snapshot tasks
            # which do not use QueueOnce. Existing industry lock keys stay intact.
            key = f"indy_hub:token_sync:{user_id}:{character_id}:{capability}"
            owner = uuid4().hex
            if not cache.add(key, owner, timeout=60):
                continue
            try:
                result = publish()
                if result is False and cache.get(key) == owner:
                    cache.delete(key)
            except Exception:
                if cache.get(key) == owner:
                    cache.delete(key)
                logger.exception(
                    "Unable to publish token sync for user %s character %s scope %s",
                    user_id,
                    character_id,
                    capability,
                )
    except Exception:
        # No inline ESI fallback or false success when cache/broker is unavailable.
        logger.exception("Unable to schedule token sync for user %s", user_id)
