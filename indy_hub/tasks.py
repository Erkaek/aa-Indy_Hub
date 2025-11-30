"""
Celery tasks for periodic ESI data updates
Following AllianceAuth best practices

This module serves as the main entry point for all Celery tasks.
Tasks are organized in specialized modules under the tasks/ directory.
"""

# Standard Library
import logging

# Django
from django.contrib.auth.models import User  # noqa: F401

# Indy Hub
from .models import Blueprint, IndustryJob  # noqa: F401

# Import the setup function from tasks module
from .tasks import setup_periodic_tasks  # noqa: F401

# Import all tasks from specialized modules
from .tasks.industry import (  # noqa: F401
    cleanup_old_jobs,
    update_all_blueprints,
    update_all_industry_jobs,
    update_blueprints_for_user,
    update_industry_jobs_for_user,
    update_type_names,
)
from .tasks.location import refresh_structure_location  # noqa: F401
from .tasks.notifications import (  # noqa: F401
    dispatch_job_notification_digests,
    notify_recently_completed_jobs,
)
from .tasks.user import *  # noqa: F401, F403

logger = logging.getLogger(__name__)

# All tasks are imported above and available for use
# The setup_periodic_tasks function is imported from tasks/__init__.py
# This provides a clean separation of concerns while maintaining backwards compatibility
