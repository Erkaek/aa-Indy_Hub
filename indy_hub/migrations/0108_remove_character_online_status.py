# Django
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0107_industrystructure_resolved_bonus_cache"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CharacterOnlineStatus",
        ),
    ]
