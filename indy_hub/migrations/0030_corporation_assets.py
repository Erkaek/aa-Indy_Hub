# Generated manually by ChatGPT
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations, models
from django.db.models import Q


def populate_owner_kind(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    Blueprint.objects.filter(owner_kind="").update(owner_kind="character")
    Blueprint.objects.filter(owner_kind__isnull=True).update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind="").update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind__isnull=True).update(owner_kind="character")


def purge_default_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(content_type__app_label="indy_hub").filter(
        Q(codename__startswith="add_")
        | Q(codename__startswith="change_")
        | Q(codename__startswith="delete_")
        | Q(codename__startswith="view_")
    ).delete()


def ensure_custom_permissions(apps, schema_editor):
    app_config = global_apps.get_app_config("indy_hub")
    create_permissions(app_config, verbosity=0)


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0029_alter_useronboardingprogress_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="blueprint",
            name="corporation_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="blueprint",
            name="corporation_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="blueprint",
            name="owner_kind",
            field=models.CharField(
                choices=[
                    ("character", "Character-owned"),
                    ("corporation", "Corporation-owned"),
                ],
                default="character",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="blueprint",
            name="character_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="industryjob",
            name="corporation_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="industryjob",
            name="corporation_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="industryjob",
            name="owner_kind",
            field=models.CharField(
                choices=[
                    ("character", "Character-owned"),
                    ("corporation", "Corporation-owned"),
                ],
                default="character",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="industryjob",
            name="character_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="blueprint",
            options={
                "db_table": "indy_hub_indyblueprint",
                "default_permissions": (),
                "permissions": (
                    ("can_access_indy_hub", "Can access Indy Hub module"),
                    (
                        "can_manage_copy_requests",
                        "Can request or share blueprint copies",
                    ),
                    (
                        "can_manage_corporate_assets",
                        "Can manage corporation blueprints and jobs",
                    ),
                ),
                "verbose_name": "Blueprint",
                "verbose_name_plural": "Blueprints",
            },
        ),
        migrations.AddIndex(
            model_name="blueprint",
            index=models.Index(
                fields=["owner_kind", "corporation_id", "type_id"],
                name="indy_hub_bl_corp_scope_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industryjob",
            index=models.Index(
                fields=["owner_kind", "corporation_id", "status"],
                name="indy_hub_in_corp_scope_idx",
            ),
        ),
        migrations.RunPython(populate_owner_kind, migrations.RunPython.noop),
        migrations.RunPython(purge_default_permissions, migrations.RunPython.noop),
        migrations.RunPython(ensure_custom_permissions, migrations.RunPython.noop),
    ]
