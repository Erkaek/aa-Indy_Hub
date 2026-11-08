"""Small fixture helpers for the isolated, serial test databases."""

# Django
from django.db.models import Max


def create_raw_fixture(instance):
    """Insert without save/signals and retain a usable integer primary key.

    MySQL does not populate generated keys after bulk_create(). Allocate the
    key explicitly in our private, serial test database so related fixtures
    work on SQLite, MySQL and MariaDB without token-link or task signals.
    This allocation is only suitable for isolated test fixtures.
    """
    manager = type(instance).objects
    if instance.pk is None:
        instance.pk = (manager.aggregate(last_pk=Max("pk"))["last_pk"] or 0) + 1
    manager.bulk_create([instance])
    return instance
