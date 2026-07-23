# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility no-op migration kept to avoid update conflicts.

    Some production installs upgraded through intermediate package builds that
    exposed this migration name as an alternate branch from 0100. Keeping this
    no-op file in the canonical migration set allows Django to resolve historical
    states cleanly instead of raising a multiple-leaf conflict during `migrate`.
    """

    dependencies = [
        ("indy_hub", "0100_repair_blueprint_bp_type_classification"),
    ]

    operations = []
