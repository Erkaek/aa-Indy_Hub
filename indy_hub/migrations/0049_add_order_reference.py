# Django
from django.db import migrations, models


def add_order_reference_columns(apps, schema_editor):
    """Add order_reference columns using raw SQL if they don't exist."""
    # Django
    from django.db import connection
    from django.db.backends.base.base import BaseDatabaseWrapper

    vendor = connection.vendor

    with connection.cursor() as cursor:
        # Handle MySQL and SQLite differently
        if vendor == "mysql":
            # Check and add to sell orders
            cursor.execute(
                """
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'indy_hub_materialexchangesellorder'
                AND COLUMN_NAME = 'order_reference'
            """
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    ALTER TABLE indy_hub_materialexchangesellorder
                    ADD COLUMN order_reference VARCHAR(50) DEFAULT '' NOT NULL
                """
                )

            # Check and add to buy orders
            cursor.execute(
                """
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'indy_hub_materialexchangebuyorder'
                AND COLUMN_NAME = 'order_reference'
            """
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    ALTER TABLE indy_hub_materialexchangebuyorder
                    ADD COLUMN order_reference VARCHAR(50) DEFAULT '' NOT NULL
                """
                )
        elif vendor == "sqlite":
            # SQLite doesn't have INFORMATION_SCHEMA, use pragma instead
            cursor.execute("PRAGMA table_info(indy_hub_materialexchangesellorder)")
            columns = [row[1] for row in cursor.fetchall()]
            if "order_reference" not in columns:
                cursor.execute(
                    """
                    ALTER TABLE indy_hub_materialexchangesellorder
                    ADD COLUMN order_reference VARCHAR(50) DEFAULT ''
                """
                )

            cursor.execute("PRAGMA table_info(indy_hub_materialexchangebuyorder)")
            columns = [row[1] for row in cursor.fetchall()]
            if "order_reference" not in columns:
                cursor.execute(
                    """
                    ALTER TABLE indy_hub_materialexchangebuyorder
                    ADD COLUMN order_reference VARCHAR(50) DEFAULT ''
                """
                )


def reverse_add_order_reference_columns(apps, schema_editor):
    """No-op reverse - we can't safely drop columns."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0048_remove_market_group_filters"),
    ]

    operations = [
        # Add columns if they don't exist
        migrations.RunPython(
            add_order_reference_columns,
            reverse_add_order_reference_columns,
        ),
        # Sync state - columns already exist from RunPython, just update migration state
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="materialexchangesellorder",
                    name="order_reference",
                    field=models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Unique order reference (INDY-{id}) for contract matching",
                        max_length=50,
                        default="",
                    ),
                ),
                migrations.AddField(
                    model_name="materialexchangebuyorder",
                    name="order_reference",
                    field=models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Unique order reference (INDY-{id}) for contract matching",
                        max_length=50,
                        default="",
                    ),
                ),
            ],
            database_operations=[
                # Columns already exist, no database ops needed
            ],
        ),
    ]
