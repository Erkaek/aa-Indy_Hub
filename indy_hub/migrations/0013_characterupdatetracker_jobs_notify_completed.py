# Generated by Django 4.2.21 on 2025-06-08 23:02

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0012_industryjob_job_completed_notified"),
    ]

    operations = [
        migrations.AddField(
            model_name="characterupdatetracker",
            name="jobs_notify_completed",
            field=models.BooleanField(default=True),
        ),
    ]
