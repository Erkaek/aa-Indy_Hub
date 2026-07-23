# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility no-op migration kept to avoid update conflicts.

    Some installations include this historical migration name as an alternate
    branch from 0096. Keeping this node in the canonical migration graph avoids
    multiple-leaf conflicts during upgrades.
    """

    dependencies = [
        ("indy_hub", "0096_add_production_projects"),
    ]

    operations = []
