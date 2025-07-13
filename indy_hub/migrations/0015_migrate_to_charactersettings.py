# Generated migration to transfer existing CharacterUpdateTracker and BlueprintCopyShareSetting data
# Django
from django.conf import settings
from django.db import migrations, models


def migrate_preferences(apps, schema_editor):
    CharacterSettings = apps.get_model("indy_hub", "CharacterSettings")
    CharacterUpdateTracker = apps.get_model("indy_hub", "CharacterUpdateTracker")
    BlueprintCopyShareSetting = apps.get_model("indy_hub", "BlueprintCopyShareSetting")

    # Transfer job notification settings
    for tracker in CharacterUpdateTracker.objects.all():
        cs, _ = CharacterSettings.objects.get_or_create(
            user_id=tracker.user_id,
            character_id=tracker.character_id,
            defaults={
                "jobs_notify_completed": tracker.jobs_notify_completed,
            },
        )
        if cs.jobs_notify_completed != tracker.jobs_notify_completed:
            cs.jobs_notify_completed = tracker.jobs_notify_completed
            cs.save(update_fields=["jobs_notify_completed"])

    # Transfer copy sharing settings (global, character_id=0)
    for share in BlueprintCopyShareSetting.objects.all():
        cs, _ = CharacterSettings.objects.get_or_create(
            user_id=share.user_id,
            character_id=0,
            defaults={
                "allow_copy_requests": share.allow_copy_requests,
            },
        )
        if cs.allow_copy_requests != share.allow_copy_requests:
            cs.allow_copy_requests = share.allow_copy_requests
            cs.save(update_fields=["allow_copy_requests"])


class Migration(migrations.Migration):

    dependencies = [
        (
            "indy_hub",
            "0014_alter_blueprint_options_and_more",
        ),  # replace with actual last migration name
    ]

    operations = [
        # Create new CharacterSettings model table before migrating data
        migrations.CreateModel(
            name="CharacterSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("character_id", models.BigIntegerField()),
                ("jobs_notify_completed", models.BooleanField(default=False)),
                ("allow_copy_requests", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "character_id")},
                "default_permissions": (),
            },
        ),
        # Transfer existing preferences
        migrations.RunPython(
            migrate_preferences, reverse_code=migrations.RunPython.noop
        ),
        # Remove old models
        migrations.DeleteModel(name="CharacterUpdateTracker"),
        migrations.DeleteModel(name="BlueprintCopyShareSetting"),
    ]
