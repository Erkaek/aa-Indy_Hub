"""Bounded, global CCP archive verification. No authenticated ESI calls."""

# Third Party
from celery import shared_task

# AA Example App
from indy_hub.services.industry_qualification import check_installed_industry_rules


@shared_task(soft_time_limit=840, time_limit=900)
def check_industry_rules():
    return check_installed_industry_rules()
