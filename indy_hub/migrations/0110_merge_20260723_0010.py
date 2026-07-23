# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Merge compatibility branch 0098_merge into the current migration line."""

    dependencies = [
        ("indy_hub", "0109_merge_20260723_0001"),
        ("indy_hub", "0098_merge_20260427_1304"),
    ]

    operations = []
