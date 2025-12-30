# Generated migration for Material Exchange workflow refactoring
# Adds ESI contract tracking and updated status choices for sell/buy orders

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0054_rename_indy_hub_es_corpora_c8d8a5_idx_indy_hub_es_corpora_e83e14_idx_and_more"),
    ]

    operations = [
        # Alter MaterialExchangeSellOrder status field and add new fields
        migrations.AlterField(
            model_name="materialexchangesellorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft - Awaiting Contract"),
                    ("awaiting_validation", "Awaiting Auth Validation"),
                    ("validated", "Validated - Awaiting Contract Accept"),
                    ("completed", "Completed"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="materialexchangesellorder",
            name="esi_contract_id",
            field=models.BigIntegerField(
                blank=True,
                db_index=True,
                help_text="ESI contract ID for this sell order",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="materialexchangesellorder",
            name="contract_validated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the contract was validated against this order",
                null=True,
            ),
        ),
        # Alter MaterialExchangeBuyOrder status field and add new fields
        migrations.AlterField(
            model_name="materialexchangebuyorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft - Awaiting Contract"),
                    ("awaiting_validation", "Awaiting Auth Validation"),
                    ("validated", "Validated - Awaiting User Accept"),
                    ("completed", "Completed"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="materialexchangebuyorder",
            name="esi_contract_id",
            field=models.BigIntegerField(
                blank=True,
                db_index=True,
                help_text="ESI contract ID for this buy order",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="materialexchangebuyorder",
            name="contract_validated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the contract was validated against this order",
                null=True,
            ),
        ),
        # Add index for esi_contract_id in both models
        migrations.AddIndex(
            model_name="materialexchangesellorder",
            index=models.Index(
                fields=["esi_contract_id"],
                name="indy_hub_me_esi_con_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangebuyorder",
            index=models.Index(
                fields=["esi_contract_id"],
                name="indy_hub_me_buy_esi_con_idx",
            ),
        ),
    ]
