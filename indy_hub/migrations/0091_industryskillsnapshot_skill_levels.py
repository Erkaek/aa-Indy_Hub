# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0090_sdeblueprintactivity"),
    ]

    operations = [
        migrations.AddField(
            model_name="industryskillsnapshot",
            name="skill_levels",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
