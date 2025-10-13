# Generated manually for location name population and schema cleanup
from __future__ import annotations

# Django
from django.core.exceptions import FieldDoesNotExist
from django.db import migrations, models


def _run_mysql_statements(schema_editor, statements: list[str]) -> None:
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)


def _build_add_column_statements(
    table: str, column: str, column_definition: str
) -> list[str]:
    return [
        (
            "SET @col_exists := ("
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{table}' "
            f"AND COLUMN_NAME = '{column}'"
            ")"
        ),
        (
            f"SET @ddl := IF(@col_exists = 0, \"ALTER TABLE `{table}` ADD COLUMN {column_definition}\", 'DO 0')"
        ),
        "SET @col_exists := NULL",
        "PREPARE stmt FROM @ddl",
        "EXECUTE stmt",
        "DEALLOCATE PREPARE stmt",
    ]


def _build_drop_column_statements(table: str, column: str) -> list[str]:
    return [
        (
            "SET @col_exists := ("
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{table}' "
            f"AND COLUMN_NAME = '{column}'"
            ")"
        ),
        (
            f"SET @ddl := IF(@col_exists = 1, \"ALTER TABLE `{table}` DROP COLUMN `{column}`\", 'DO 0')"
        ),
        "SET @col_exists := NULL",
        "PREPARE stmt FROM @ddl",
        "EXECUTE stmt",
        "DEALLOCATE PREPARE stmt",
    ]


_BLUEPRINT_LOCATION_ADD = _build_add_column_statements(
    "indy_hub_indyblueprint",
    "location_name",
    "`location_name` varchar(255) NOT NULL DEFAULT ''",
)

_BLUEPRINT_LOCATION_DROP = _build_drop_column_statements(
    "indy_hub_indyblueprint",
    "location_name",
)

_INDUSTRYJOB_LOCATION_ADD = _build_add_column_statements(
    "indy_hub_industryjob",
    "location_name",
    "`location_name` varchar(255) NOT NULL DEFAULT ''",
)

_INDUSTRYJOB_LOCATION_DROP = _build_drop_column_statements(
    "indy_hub_industryjob",
    "location_name",
)

_INDUSTRYJOB_FACILITY_DROP = _build_drop_column_statements(
    "indy_hub_industryjob",
    "facility_id",
)

_INDUSTRYJOB_FACILITY_ADD = _build_add_column_statements(
    "indy_hub_industryjob",
    "facility_id",
    "`facility_id` bigint NULL",
)

_INDUSTRYJOB_BLUEPRINT_LOCATION_DROP = _build_drop_column_statements(
    "indy_hub_industryjob",
    "blueprint_location_id",
)

_INDUSTRYJOB_BLUEPRINT_LOCATION_ADD = _build_add_column_statements(
    "indy_hub_industryjob",
    "blueprint_location_id",
    "`blueprint_location_id` bigint NULL",
)

_INDUSTRYJOB_OUTPUT_LOCATION_DROP = _build_drop_column_statements(
    "indy_hub_industryjob",
    "output_location_id",
)

_INDUSTRYJOB_OUTPUT_LOCATION_ADD = _build_add_column_statements(
    "indy_hub_industryjob",
    "output_location_id",
    "`output_location_id` bigint NULL",
)


def add_blueprint_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _BLUEPRINT_LOCATION_ADD)
        return

    Blueprint = apps.get_model("indy_hub", "Blueprint")
    field = models.CharField(blank=True, max_length=255)
    field.set_attributes_from_name("location_name")
    schema_editor.add_field(Blueprint, field)


def drop_blueprint_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _BLUEPRINT_LOCATION_DROP)
        return

    Blueprint = apps.get_model("indy_hub", "Blueprint")
    try:
        field = Blueprint._meta.get_field("location_name")
    except FieldDoesNotExist:
        return
    schema_editor.remove_field(Blueprint, field)


def add_industryjob_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_LOCATION_ADD)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    field = models.CharField(blank=True, max_length=255)
    field.set_attributes_from_name("location_name")
    schema_editor.add_field(IndustryJob, field)


def drop_industryjob_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_LOCATION_DROP)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    try:
        field = IndustryJob._meta.get_field("location_name")
    except FieldDoesNotExist:
        return
    schema_editor.remove_field(IndustryJob, field)


def drop_industryjob_facility_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_FACILITY_DROP)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    try:
        field = IndustryJob._meta.get_field("facility_id")
    except FieldDoesNotExist:
        return
    schema_editor.remove_field(IndustryJob, field)


def add_industryjob_facility_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_FACILITY_ADD)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    field = models.BigIntegerField(blank=True, null=True)
    field.set_attributes_from_name("facility_id")
    schema_editor.add_field(IndustryJob, field)


def drop_industryjob_blueprint_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_BLUEPRINT_LOCATION_DROP)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    try:
        field = IndustryJob._meta.get_field("blueprint_location_id")
    except FieldDoesNotExist:
        return
    schema_editor.remove_field(IndustryJob, field)


def add_industryjob_blueprint_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_BLUEPRINT_LOCATION_ADD)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    field = models.BigIntegerField(blank=True, null=True)
    field.set_attributes_from_name("blueprint_location_id")
    schema_editor.add_field(IndustryJob, field)


def drop_industryjob_output_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_OUTPUT_LOCATION_DROP)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    try:
        field = IndustryJob._meta.get_field("output_location_id")
    except FieldDoesNotExist:
        return
    schema_editor.remove_field(IndustryJob, field)


def add_industryjob_output_location_column(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        _run_mysql_statements(schema_editor, _INDUSTRYJOB_OUTPUT_LOCATION_ADD)
        return

    IndustryJob = apps.get_model("indy_hub", "IndustryJob")
    field = models.BigIntegerField(blank=True, null=True)
    field.set_attributes_from_name("output_location_id")
    schema_editor.add_field(IndustryJob, field)


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0022_alter_blueprint_bp_type"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_blueprint_location_column,
                    drop_blueprint_location_column,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="blueprint",
                    name="location_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_industryjob_location_column,
                    drop_industryjob_location_column,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="location_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    drop_industryjob_facility_column,
                    add_industryjob_facility_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="facility_id",
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    drop_industryjob_blueprint_location_column,
                    add_industryjob_blueprint_location_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="blueprint_location_id",
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    drop_industryjob_output_location_column,
                    add_industryjob_output_location_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="output_location_id",
                )
            ],
        ),
    ]
