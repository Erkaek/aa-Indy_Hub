# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0091_industryskillsnapshot_skill_levels"),
    ]

    operations = [
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="blueprint_catalog_scope",
            field=models.CharField(
                choices=[
                    ("none", "Private"),
                    ("corporation", "Corporation"),
                    ("alliance", "Alliance"),
                    ("everyone", "Everyone"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="corporationsharingsetting",
            name="job_catalog_scope",
            field=models.CharField(
                choices=[
                    ("none", "Private"),
                    ("corporation", "Corporation"),
                    ("alliance", "Alliance"),
                    ("everyone", "Everyone"),
                ],
                default="none",
                max_length=20,
            ),
        ),
    ]
