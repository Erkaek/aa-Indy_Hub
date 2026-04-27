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

SDE_FOLDER = clean_setting(
    "INDY_HUB_SDE_FOLDER",
    "eve-sde",
    required_type=str,
)

# Material Exchange / craft project forms can post thousands of fields when the
# user toggles many EVE market groups or type ids at once (well above Django's
# default `DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000`). Indy Hub raises this limit
# at app startup unless the project already configured a higher value.
MAX_FORM_FIELDS = clean_setting(
    "INDY_HUB_MAX_FORM_FIELDS",
    50000,
    min_value=1000,
    required_type=int,
)

# The craft project workspace save endpoint posts a JSON body that includes
# a full cached project payload snapshot (every blueprint card, decision row,
# material breakdown, structure list, …). For moderately complex projects this
# easily exceeds Django's default `DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440`
# (2.5 MB), which surfaces in the UI as a generic "Failed to save table"
# notification. Raise the limit so realistic projects round-trip cleanly.
MAX_REQUEST_BODY_BYTES = clean_setting(
    "INDY_HUB_MAX_REQUEST_BODY_BYTES",
    52_428_800,  # 50 MB
    min_value=2_621_440,
    required_type=int,
)

MANUAL_REFRESH_COOLDOWN_SECONDS = clean_setting(
    "INDY_HUB_MANUAL_REFRESH_COOLDOWN_SECONDS",
    300,
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
