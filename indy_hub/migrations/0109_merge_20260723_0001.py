# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Merge compatibility branch 0101_merge into the current migration line."""

    dependencies = [
        ("indy_hub", "0108_remove_character_online_status"),
        ("indy_hub", "0101_merge_20260524_1143"),
    ]

    operations = []
