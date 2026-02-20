# Django
from django.db import migrations


def rename_permission_labels(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        blueprint_ct = ContentType.objects.get(app_label="indy_hub", model="blueprint")
    except ContentType.DoesNotExist:
        return

    updates = {
        "can_access_indy_hub": "can access Indy_Hub",
        "can_manage_corp_bp_requests": "can admin Corp",
        "can_manage_material_hub": "can admin MatExchange",
    }

    for codename, name in updates.items():
        Permission.objects.filter(content_type=blueprint_ct, codename=codename).update(
            name=name
        )


def revert_permission_labels(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        blueprint_ct = ContentType.objects.get(app_label="indy_hub", model="blueprint")
    except ContentType.DoesNotExist:
        return

    updates = {
        "can_access_indy_hub": "Can access Indy Hub",
        "can_manage_corp_bp_requests": "Can manage corporation indy",
        "can_manage_material_hub": "Can manage Mat Exchange",
    }

    for codename, name in updates.items():
        Permission.objects.filter(content_type=blueprint_ct, codename=codename).update(
            name=name
        )


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0084_materialexchangesellorder_anomaly_status"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="blueprint",
            options={
                "verbose_name": "Blueprint",
                "verbose_name_plural": "Blueprints",
                "db_table": "indy_hub_indyblueprint",
                "permissions": [
                    ("can_access_indy_hub", "can access Indy_Hub"),
                    ("can_manage_corp_bp_requests", "can admin Corp"),
                    ("can_manage_material_hub", "can admin MatExchange"),
                ],
                "default_permissions": (),
            },
        ),
        migrations.RunPython(rename_permission_labels, revert_permission_labels),
    ]
