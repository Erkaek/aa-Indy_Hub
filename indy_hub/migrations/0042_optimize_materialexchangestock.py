# Generated migration for optimizing MaterialExchangeStock
# Adds indexes, audit timestamps, and last_stock_sync tracking

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0041_materialexchangeconfig_materialexchangesellorder_and_more"),
    ]

    operations = [
        # Add new fields for audit trail
        migrations.AddField(
            model_name="materialexchangestock",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name="materialexchangestock",
            name="last_stock_sync",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When this item's quantity was last synced",
            ),
        ),
        # Add db_index to type_name for search performance
        migrations.AlterField(
            model_name="materialexchangestock",
            name="type_name",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        # Add new indexes for common query patterns
        migrations.AddIndex(
            model_name="materialexchangestock",
            index=models.Index(
                fields=["config", "quantity"], name="mes_config_qty_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangestock",
            index=models.Index(
                fields=["config", "updated_at"], name="mes_config_upd_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangestock",
            index=models.Index(fields=["type_name"], name="mes_typename_idx"),
        ),
    ]
