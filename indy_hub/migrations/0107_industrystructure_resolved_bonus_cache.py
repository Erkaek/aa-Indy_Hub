from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0106_update_periodic_tasks"),
    ]

    operations = [
        migrations.AddField(
            model_name="industrystructure",
            name="resolved_bonuses_cache",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="industrystructure",
            name="resolved_bonuses_cache_signature",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="industrystructure",
            name="resolved_bonuses_cache_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
