# Django
from django.db import migrations, models


def forwards_copy_scope(apps, schema_editor):
    CharacterSettings = apps.get_model("indy_hub", "CharacterSettings")
    for settings in CharacterSettings.objects.all():
        scope = "corporation" if settings.allow_copy_requests else "none"
        if settings.copy_sharing_scope != scope:
            settings.copy_sharing_scope = scope
            settings.save(update_fields=["copy_sharing_scope"])


def backwards_copy_scope(apps, schema_editor):
    CharacterSettings = apps.get_model("indy_hub", "CharacterSettings")
    scope_map = {
        "none": False,
        "corporation": True,
        "alliance": True,
    }
    for settings in CharacterSettings.objects.all():
        allow = scope_map.get(settings.copy_sharing_scope, False)
        if settings.allow_copy_requests != allow:
            settings.allow_copy_requests = allow
            settings.save(update_fields=["allow_copy_requests"])


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0019_charactersettings_constraints"),
    ]

    operations = [
        migrations.AddField(
            model_name="charactersettings",
            name="copy_sharing_scope",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("corporation", "Corporation"),
                    ("alliance", "Alliance"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.RunPython(forwards_copy_scope, backwards_copy_scope),
    ]
