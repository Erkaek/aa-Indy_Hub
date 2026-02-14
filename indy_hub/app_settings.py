"""App settings for indy_hub using AppUtils clean_setting."""

from __future__ import annotations

# Alliance Auth (External Libs)
from app_utils.app_settings import clean_setting

# AA Example App
from indy_hub import __esi_compatibility_date__

DISCORD_DM_ENABLED = clean_setting(
    "INDY_HUB_DISCORD_DM_ENABLED",
    True,
    required_type=bool,
)
DISCORD_FOOTER_TEXT = clean_setting(
    "INDY_HUB_DISCORD_FOOTER_TEXT",
    "",
    required_type=str,
)
DISCORD_ACTION_TOKEN_MAX_AGE = clean_setting(
    "INDY_HUB_DISCORD_ACTION_TOKEN_MAX_AGE",
    86400,
    min_value=60,
    required_type=int,
)

SITE_URL = clean_setting(
    "INDY_HUB_SITE_URL",
    "",
    required_type=str,
)

ESI_COMPATIBILITY_DATE = clean_setting(
    "INDY_HUB_ESI_COMPATIBILITY_DATE",
    __esi_compatibility_date__,
    required_type=str,
)

MANUAL_REFRESH_COOLDOWN_SECONDS = clean_setting(
    "INDY_HUB_MANUAL_REFRESH_COOLDOWN_SECONDS",
    3600,
    min_value=0,
    required_type=int,
)

ROLES_CACHE_MAX_AGE_MINUTES = clean_setting(
    "INDY_HUB_ROLES_CACHE_MAX_AGE_MINUTES",
    60,
    min_value=0,
    required_type=int,
)

ASSET_CACHE_MAX_AGE_MINUTES = clean_setting(
    "INDY_HUB_ASSET_CACHE_MAX_AGE_MINUTES",
    60,
    min_value=0,
    required_type=int,
)
CHAR_ASSET_CACHE_MAX_AGE_MINUTES = clean_setting(
    "INDY_HUB_CHAR_ASSET_CACHE_MAX_AGE_MINUTES",
    ASSET_CACHE_MAX_AGE_MINUTES,
    min_value=0,
    required_type=int,
)
DIVISION_CACHE_MAX_AGE_MINUTES = clean_setting(
    "INDY_HUB_DIVISION_CACHE_MAX_AGE_MINUTES",
    1440,
    min_value=0,
    required_type=int,
)

ONLINE_STATUS_STALE_HOURS = clean_setting(
    "INDY_HUB_ONLINE_STATUS_STALE_HOURS",
    72,
    min_value=1,
    required_type=int,
)

SKILL_SNAPSHOT_STALE_HOURS = clean_setting(
    "INDY_HUB_SKILL_SNAPSHOT_STALE_HOURS",
    24,
    min_value=1,
    required_type=int,
)

ROLE_SNAPSHOT_STALE_HOURS = clean_setting(
    "INDY_HUB_ROLE_SNAPSHOT_STALE_HOURS",
    24,
    min_value=1,
    required_type=int,
)

STRUCTURE_NAME_STALE_HOURS = clean_setting(
    "INDY_HUB_STRUCTURE_NAME_STALE_HOURS",
    24,
    min_value=1,
    required_type=int,
)

LOCATION_LOOKUP_BUDGET = clean_setting(
    "INDY_HUB_LOCATION_LOOKUP_BUDGET",
    50,
    min_value=0,
    required_type=int,
)

BLUEPRINTS_BULK_WINDOW_MINUTES = clean_setting(
    "INDY_HUB_BLUEPRINTS_BULK_WINDOW_MINUTES",
    720,
    min_value=0,
    required_type=int,
)
INDUSTRY_JOBS_BULK_WINDOW_MINUTES = clean_setting(
    "INDY_HUB_INDUSTRY_JOBS_BULK_WINDOW_MINUTES",
    60,
    min_value=0,
    required_type=int,
)
BULK_UPDATE_WINDOW_MINUTES = clean_setting(
    "INDY_HUB_BULK_UPDATE_WINDOW_MINUTES",
    720,
    min_value=0,
    required_type=int,
)

ESI_TASK_STAGGER_THRESHOLD = clean_setting(
    "INDY_HUB_ESI_TASK_STAGGER_THRESHOLD",
    400,
    min_value=0,
    required_type=int,
)
ESI_TASK_TARGET_PER_MIN_BLUEPRINTS = clean_setting(
    "INDY_HUB_ESI_TASK_TARGET_PER_MIN_BLUEPRINTS",
    30,
    min_value=0,
    required_type=int,
)
ESI_TASK_TARGET_PER_MIN_JOBS = clean_setting(
    "INDY_HUB_ESI_TASK_TARGET_PER_MIN_JOBS",
    30,
    min_value=0,
    required_type=int,
)
ESI_TASK_TARGET_PER_MIN_SKILLS = clean_setting(
    "INDY_HUB_ESI_TASK_TARGET_PER_MIN_SKILLS",
    40,
    min_value=0,
    required_type=int,
)
ESI_TASK_TARGET_PER_MIN_ROLES = clean_setting(
    "INDY_HUB_ESI_TASK_TARGET_PER_MIN_ROLES",
    30,
    min_value=0,
    required_type=int,
)
