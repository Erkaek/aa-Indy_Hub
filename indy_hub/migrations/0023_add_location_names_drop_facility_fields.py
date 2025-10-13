# Generated manually for location name population and schema cleanup
from __future__ import annotations

# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0022_alter_blueprint_bp_type"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_indyblueprint'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        "    \"ALTER TABLE `indy_hub_indyblueprint` ADD COLUMN `location_name` varchar(255) NOT NULL DEFAULT ''\",\n"
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_indyblueprint'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_indyblueprint` DROP COLUMN `location_name`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
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
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        "    \"ALTER TABLE `indy_hub_industryjob` ADD COLUMN `location_name` varchar(255) NOT NULL DEFAULT ''\",\n"
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `location_name`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
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
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'facility_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `facility_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'facility_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `facility_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
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
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'blueprint_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `blueprint_location_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'blueprint_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `blueprint_location_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
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
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'output_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `output_location_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'output_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `output_location_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
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
