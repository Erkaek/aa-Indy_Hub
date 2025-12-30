# Generated manually for ESI contract caching

# Django
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0052_alter_materialexchangebuyorder_order_reference_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ESIContract",
            fields=[
                (
                    "contract_id",
                    models.BigIntegerField(
                        primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("issuer_id", models.BigIntegerField(db_index=True)),
                ("issuer_corporation_id", models.BigIntegerField()),
                ("assignee_id", models.BigIntegerField(db_index=True)),
                ("acceptor_id", models.BigIntegerField(default=0)),
                ("contract_type", models.CharField(db_index=True, max_length=50)),
                ("status", models.CharField(db_index=True, max_length=50)),
                ("title", models.TextField(blank=True)),
                ("start_location_id", models.BigIntegerField(blank=True, null=True)),
                ("end_location_id", models.BigIntegerField(blank=True, null=True)),
                (
                    "price",
                    models.DecimalField(decimal_places=2, default=0, max_digits=20),
                ),
                (
                    "reward",
                    models.DecimalField(decimal_places=2, default=0, max_digits=20),
                ),
                (
                    "collateral",
                    models.DecimalField(decimal_places=2, default=0, max_digits=20),
                ),
                ("date_issued", models.DateTimeField()),
                ("date_expired", models.DateTimeField()),
                ("date_accepted", models.DateTimeField(blank=True, null=True)),
                ("date_completed", models.DateTimeField(blank=True, null=True)),
                (
                    "corporation_id",
                    models.BigIntegerField(
                        db_index=True,
                        help_text="Corporation this contract belongs to",
                    ),
                ),
                ("last_synced", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "ESI Contract",
                "verbose_name_plural": "ESI Contracts",
                "ordering": ["-date_issued"],
            },
        ),
        migrations.CreateModel(
            name="ESIContractItem",
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
                ("record_id", models.BigIntegerField(help_text="ESI record_id for this item")),
                ("type_id", models.IntegerField(db_index=True)),
                ("quantity", models.BigIntegerField()),
                (
                    "is_included",
                    models.BooleanField(
                        default=False, help_text="Item is given by issuer"
                    ),
                ),
                ("is_singleton", models.BooleanField(default=False)),
                ("last_synced", models.DateTimeField(auto_now=True)),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="indy_hub.esicontract",
                    ),
                ),
            ],
            options={
                "verbose_name": "ESI Contract Item",
                "verbose_name_plural": "ESI Contract Items",
            },
        ),
        migrations.AddIndex(
            model_name="esicontract",
            index=models.Index(
                fields=["corporation_id", "status", "contract_type"],
                name="indy_hub_es_corpora_c8d8a5_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="esicontract",
            index=models.Index(
                fields=["issuer_id", "status"], name="indy_hub_es_issuer__9c8e3e_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="esicontract",
            index=models.Index(
                fields=["acceptor_id", "contract_type"],
                name="indy_hub_es_accepto_6f7c4a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="esicontract",
            index=models.Index(fields=["-date_issued"], name="indy_hub_es_date_is_2e1f5b_idx"),
        ),
        migrations.AddIndex(
            model_name="esicontractitem",
            index=models.Index(
                fields=["contract", "type_id"], name="indy_hub_es_contrac_7a2b3c_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="esicontractitem",
            index=models.Index(
                fields=["type_id", "is_included"], name="indy_hub_es_type_id_4d5e6f_idx"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="esicontractitem",
            unique_together={("contract", "record_id")},
        ),
    ]
