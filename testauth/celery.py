"""
Celery config
"""

# Standard Library
import os

# Third Party
from celery import Celery

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testauth.settings.local")

# Django
from django.conf import settings  # noqa: E402

app = Celery("testauth")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object("django.conf:settings")

# Automatically try to establish the connection to the AMQP broker on
# Celery startup if it is unavailable.
app.conf.broker_connection_retry_on_startup = True

# setup priorities (0 Highest, 9 Lowest)
app.conf.broker_transport_options = {
    "priority_steps": list(range(10)),  # setup que to have 10 steps
    "queue_order_strategy": "priority",  # setup que to use prio sorting
}
app.conf.task_default_priority = (
    5  # anything called with the task.delay() will be given normal priority (5)
)
app.conf.worker_prefetch_multiplier = (
    1  # only prefetch single tasks at a time on the workers so that prio tasks happen
)

app.conf.ONCE = {"backend": "allianceauth.services.tasks.DjangoBackend", "settings": {}}

# Load task modules from all registered Django app configs.
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
