# Django
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0008_add_copies_requested"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlueprintCopyOffer",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("accepted", "Accepted"),
                            ("conditional", "Conditional"),
                            ("rejected", "Rejected"),
                        ],
                    ),
                ),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("accepted_by_buyer", models.BooleanField(default=False)),
                ("accepted_at", models.DateTimeField(null=True, blank=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bp_copy_offers",
                        to="auth.user",
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="offers",
                        to="indy_hub.blueprintcopyrequest",
                    ),
                ),
            ],
            options={
                "unique_together": {("request", "owner")},
            },
        ),
    ]
