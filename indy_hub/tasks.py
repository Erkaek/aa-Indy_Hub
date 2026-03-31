"""
Celery tasks for periodic ESI data updates
Following AllianceAuth best practices

This module serves as the main entry point for all Celery tasks.
Tasks are organized in specialized modules under the tasks/ directory.
"""

# Standard Library
# Django
from django.contrib.auth import get_user_model

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Indy Hub
from .models import Blueprint, IndustryJob  # noqa: F401

# Import the setup function from tasks module
from .tasks import setup_periodic_tasks  # noqa: F401
from .tasks.housekeeping import refresh_stale_snapshots  # noqa: F401

# Import all tasks from specialized modules
from .tasks.industry import (  # noqa: F401
    cleanup_old_jobs,
    update_all_blueprints,
    update_all_industry_jobs,
    update_blueprints_for_user,
    update_industry_jobs_for_user,
    update_type_names,
    update_user_skill_snapshots,
)
from .tasks.industry_structure_sync import (  # noqa: F401
    sync_persisted_industry_structure_registry,
)
from .tasks.location import refresh_structure_location  # noqa: F401
from .tasks.notifications import (  # noqa: F401
    dispatch_job_notification_digests,
    notify_recently_completed_jobs,
)
from .tasks.sde_sync import sync_sde_compatibility_data  # noqa: F401
from .tasks.system_cost_indices import sync_industry_system_cost_indices  # noqa: F401
from .tasks.user import (  # noqa: F401
    update_character_roles_for_character,
    update_user_roles_snapshots,
)

User = get_user_model()

logger = get_extension_logger(__name__)

# All tasks are imported above and available for use
# The setup_periodic_tasks function is imported from tasks/__init__.py
# This provides a clean separation of concerns while maintaining backwards compatibility
