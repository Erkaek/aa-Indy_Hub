from django.db import migrations, models
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [
        ('indy_hub', '0009_blueprintcopyoffer'),
    ]

    operations = [
        migrations.AddField(
            model_name='blueprintcopyrequest',
            name='delivered',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='blueprintcopyrequest',
            name='delivered_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
