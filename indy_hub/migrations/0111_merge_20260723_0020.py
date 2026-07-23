# Django
from django.db import migrations


class Migration(migrations.Migration):
    """Merge historical 0097 rename compatibility branch into current line."""

    dependencies = [
        ("indy_hub", "0110_merge_20260723_0010"),
        (
            "indy_hub",
            "0097_rename_indy_hub_in_constel_22f76f_idx_indy_hub_in_constel_30b149_idx_and_more",
        ),
    ]

    operations = []
