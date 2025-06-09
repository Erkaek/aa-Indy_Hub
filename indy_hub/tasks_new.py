"""
Celery tasks for periodic ESI data updates
Following AllianceAuth best practices

This module serves as the main entry point for all Celery tasks.
Tasks are organized in specialized modules under the tasks/ directory.
"""

# Standard Library
import logging

# Django
from django.contrib.auth.models import User

# Indy Hub
from .models import Blueprint, IndustryJob, CharacterUpdateTracker

# Import all tasks from specialized modules
from .tasks.industry import (
    update_blueprints_for_user, 
    update_industry_jobs_for_user, 
    notify_completed_jobs,
    update_all_blueprints,
    update_all_industry_jobs,
    cleanup_old_jobs,
    update_type_names
)
from .tasks.user import *

# Import the setup function from tasks module
from .tasks import setup_periodic_tasks

logger = logging.getLogger(__name__)

# All tasks are imported above and available for use
# The setup_periodic_tasks function is imported from tasks/__init__.py
# This provides a clean separation of concerns while maintaining backwards compatibility
