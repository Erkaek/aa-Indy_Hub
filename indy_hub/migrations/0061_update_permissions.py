# Generated migration to cleanup permissions and update model options

from django.db import migrations


def cleanup_all_permissions(apps, schema_editor):
    """Remove all unwanted Django auto-generated permissions from indy_hub"""
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    try:
        # Get all indy_hub content types
        indy_hub_content_types = ContentType.objects.filter(app_label="indy_hub")

        # All models with add/change/delete/view permissions to remove
        models_to_clean = [
            # Blueprint & Copy System
            "blueprint",
            "industryjob",
            "blueprintcopyrequest",
            "blueprintcopyoffer",
            "blueprintcopychat",
            "blueprintcopymessage",
            "blueprintcopyrequestevent",
            # Character & Corporation Settings
            "characterupdatetracker",
            "charactersettings",
            "corporationsharingsetting",
            "useronboardingprogress",
            "jobnotificationdigestentry",
            # Production System
            "productionconfig",
            "blueprintefficiency",
            "customprice",
            "productionsimulation",
            # Material Exchange System
            "materialexchangeconfig",
            "materialexchangestock",
            "materialexchangesellorder",
            "materialexchangesellorderitem",
            "materialexchangebuyorder",
            "materialexchangebuyorderitem",
            "materialexchangetransaction",
            # Cached Data Models
            "cachedcharacterasset",
            "cachedcorporationasset",
            "cachedcorporationdivision",
            "cachedstructurename",
            # ESI Contract Models
            "esicontract",
            "esicontractitem",
        ]

        # Django auto-generates these permissions for each model
        permission_prefixes = ["add_", "change_", "delete_", "view_"]

        # Build list of codenames to delete
        codenames_to_delete = []
        for model_name in models_to_clean:
            for prefix in permission_prefixes:
                codenames_to_delete.append(f"{prefix}{model_name}")

        # Delete all unwanted permissions
        Permission.objects.filter(
            content_type__in=indy_hub_content_types,
            codename__in=codenames_to_delete,
        ).delete()

    except Exception as e:
        raise


def remove_old_custom_permissions(apps, schema_editor):
    """Remove old custom permission codenames that were renamed"""
    Permission = apps.get_model("auth", "Permission")

    old_codenames = [
        "can_manage_copy_requests",
        "can_manage_corporate_assets",
        "can_manage_material_exchange",
    ]

    try:
        Permission.objects.filter(
            codename__in=old_codenames
        ).delete()

    except Exception as e:
        raise


def update_corp_permission_description(apps, schema_editor):
    """Update the can_manage_corp_bp_requests permission description"""
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    
    try:
        blueprint_ct = ContentType.objects.get(app_label='indy_hub', model='blueprint')
        permission = Permission.objects.get(
            content_type=blueprint_ct,
            codename='can_manage_corp_bp_requests'
        )
        permission.name = "Can manage corporation indy"
        permission.save()
    except (Permission.DoesNotExist, ContentType.DoesNotExist):
        pass


def update_material_permission_description(apps, schema_editor):
    """Update the can_manage_material_hub permission description"""
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    
    try:
        blueprint_ct = ContentType.objects.get(app_label='indy_hub', model='blueprint')
        permission = Permission.objects.get(
            content_type=blueprint_ct,
            codename='can_manage_material_hub'
        )
        permission.name = "Can manage Mat Exchange"
        permission.save()
    except (Permission.DoesNotExist, ContentType.DoesNotExist):
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0060_cachedcorporationasset_item_id"),
    ]

    operations = [
        # Update Blueprint model to have custom permissions
        migrations.AlterModelOptions(
            name="blueprint",
            options={
                "verbose_name": "Blueprint",
                "verbose_name_plural": "Blueprints",
                "db_table": "indy_hub_indyblueprint",
                "permissions": [
                    ("can_access_indy_hub", "Can access Indy Hub"),
                    ("can_manage_corp_bp_requests", "Can manage corporation indy"),
                    ("can_manage_material_hub", "Can manage Mat Exchange"),
                ],
                "default_permissions": (),
            },
        ),
        # Update all other models to have default_permissions = ()
        migrations.AlterModelOptions(
            name="cachedcharacterasset",
            options={"default_permissions": (), "verbose_name": "Cached Character Asset", "verbose_name_plural": "Cached Character Assets"},
        ),
        migrations.AlterModelOptions(
            name="cachedcorporationasset",
            options={"default_permissions": (), "verbose_name": "Cached Corporation Asset", "verbose_name_plural": "Cached Corporation Assets"},
        ),
        migrations.AlterModelOptions(
            name="cachedcorporationdivision",
            options={"default_permissions": ()},
        ),
        migrations.AlterModelOptions(
            name="cachedstructurename",
            options={"default_permissions": (), "verbose_name": "Cached Structure Name", "verbose_name_plural": "Cached Structure Names"},
        ),
        migrations.AlterModelOptions(
            name="esicontract",
            options={"default_permissions": (), "verbose_name": "ESI Contract", "verbose_name_plural": "ESI Contracts"},
        ),
        migrations.AlterModelOptions(
            name="esicontractitem",
            options={"default_permissions": (), "verbose_name": "ESI Contract Item", "verbose_name_plural": "ESI Contract Items"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangebuyorder",
            options={"default_permissions": (), "verbose_name": "Material Exchange Buy Order", "verbose_name_plural": "Material Exchange Buy Orders"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangebuyorderitem",
            options={"default_permissions": (), "verbose_name": "Material Exchange Buy Order Item", "verbose_name_plural": "Material Exchange Buy Order Items"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangeconfig",
            options={"default_permissions": (), "verbose_name": "Material Exchange Configuration", "verbose_name_plural": "Material Exchange Configurations"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangesellorder",
            options={"default_permissions": (), "verbose_name": "Material Exchange Sell Order", "verbose_name_plural": "Material Exchange Sell Orders"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangesellorderitem",
            options={"default_permissions": (), "verbose_name": "Material Exchange Sell Order Item", "verbose_name_plural": "Material Exchange Sell Order Items"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangestock",
            options={"default_permissions": (), "verbose_name": "Material Exchange Stock", "verbose_name_plural": "Material Exchange Stock"},
        ),
        migrations.AlterModelOptions(
            name="materialexchangetransaction",
            options={"default_permissions": (), "verbose_name": "Material Exchange Transaction", "verbose_name_plural": "Material Exchange Transactions"},
        ),
        # Clean up permissions (step 1)
        migrations.RunPython(cleanup_all_permissions, migrations.RunPython.noop),
        # Remove old custom permissions (step 2)
        migrations.RunPython(remove_old_custom_permissions, migrations.RunPython.noop),
        # Update corp permission description (step 3)
        migrations.RunPython(update_corp_permission_description, migrations.RunPython.noop),
        # Update material permission description (step 4)
        migrations.RunPython(update_material_permission_description, migrations.RunPython.noop),
    ]
