# Django
from django.db import migrations, models


def forward_copy_user_corp_prefs(apps, schema_editor):
    CharacterSettings = apps.get_model("indy_hub", "CharacterSettings")
    CorporationSharingSetting = apps.get_model("indy_hub", "CorporationSharingSetting")

    # Best-effort: copy the user-level corp cadence settings onto each existing
    # per-corp sharing row for that user, so existing installs keep behavior.
    by_user = {}
    for cs in CharacterSettings.objects.all().only(
        "user_id",
        "corp_jobs_notify_frequency",
        "corp_jobs_notify_custom_days",
        "corp_jobs_next_digest_at",
        "corp_jobs_last_digest_at",
    ):
        # If multiple CharacterSettings exist per user, keep the first.
        by_user.setdefault(cs.user_id, cs)

    to_update = []
    for row in CorporationSharingSetting.objects.all().only(
        "id",
        "user_id",
        "corp_jobs_notify_frequency",
        "corp_jobs_notify_custom_days",
        "corp_jobs_next_digest_at",
        "corp_jobs_last_digest_at",
    ):
        cs = by_user.get(row.user_id)
        if not cs:
            continue
        row.corp_jobs_notify_frequency = cs.corp_jobs_notify_frequency
        row.corp_jobs_notify_custom_days = cs.corp_jobs_notify_custom_days
        row.corp_jobs_next_digest_at = cs.corp_jobs_next_digest_at
        row.corp_jobs_last_digest_at = cs.corp_jobs_last_digest_at
        to_update.append(row)

    if to_update:
        CorporationSharingSetting.objects.bulk_update(
            to_update,
            [
                "corp_jobs_notify_frequency",
                "corp_jobs_notify_custom_days",
                "corp_jobs_next_digest_at",
                "corp_jobs_last_digest_at",
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0064_corp_job_notifications_and_digest_scope"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="jobnotificationdigestentry",
            name="job_digest_user_job_scope_uq",
        ),
        migrations.AddField(
            model_name="jobnotificationdigestentry",
            name="corporation_id",
            field=models.BigIntegerField(db_index=True, default=0),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="jobnotificationdigestentry",
            constraint=models.UniqueConstraint(
                fields=("user", "job_id", "scope", "corporation_id"),
                name="job_digest_user_job_scope_corp_uq",
            ),
        ),
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="corp_jobs_last_digest_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="corp_jobs_next_digest_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="corp_jobs_notify_custom_days",
            field=models.PositiveSmallIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="corp_jobs_notify_frequency",
            field=models.CharField(
                choices=[
                    ("disabled", "Disabled"),
                    ("immediate", "Immediate"),
                    ("daily", "Daily digest"),
                    ("weekly", "Weekly digest"),
                    ("monthly", "Monthly digest"),
                    ("custom", "Custom cadence"),
                ],
                default="disabled",
                max_length=20,
            ),
        ),
        migrations.RunPython(forward_copy_user_corp_prefs, migrations.RunPython.noop),
    ]
