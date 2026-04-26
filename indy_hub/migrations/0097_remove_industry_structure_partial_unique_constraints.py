# Django
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0096_add_production_projects"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="industrystructure",
            name="indy_hub_structure_public_name_uq",
        ),
        migrations.RemoveConstraint(
            model_name="industrystructure",
            name="indy_hub_structure_personal_owner_name_tag_uq",
        ),
    ]
