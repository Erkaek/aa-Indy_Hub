# Django
from django.db import connection, migrations


def fix_reaction_activity_id(apps, schema_editor):
    with connection.cursor() as cursor:
        # Vérifie qu'il existe une activité 'Reactions' avec id=9
        cursor.execute(
            "SELECT COUNT(*) FROM eveuniverse_eveindustryactivity WHERE id=9 AND LOWER(name)='reactions'"
        )
        if cursor.fetchone()[0]:
            # Vérifie qu'il n'y a pas déjà un id=11
            cursor.execute(
                "SELECT COUNT(*) FROM eveuniverse_eveindustryactivity WHERE id=11"
            )
            if not cursor.fetchone()[0]:
                # Met à jour l'id de 9 à 11
                cursor.execute(
                    "UPDATE eveuniverse_eveindustryactivity SET id=11 WHERE id=9 AND LOWER(name)='reactions'"
                )


def noop_reverse(apps, schema_editor):
    # No-op reverse for migration rollback compatibility
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0004_alter_characterupdatetracker_options_and_more"),
        ("eveuniverse", "0010_alter_eveindustryactivityduration_eve_type_and_more"),
    ]

    operations = [
        migrations.RunPython(fix_reaction_activity_id, reverse_code=noop_reverse),
    ]
