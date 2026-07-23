# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility no-op migration kept to avoid update conflicts.

    Some installations upgraded through intermediate package builds where this
    migration existed as an alternate branch from 0097. Keeping the same node
    in the canonical migration set prevents multiple-leaf conflicts on upgrade.
    """

    dependencies = [
        ("indy_hub", "0097_remove_industry_structure_partial_unique_constraints"),
    ]

    operations = []
