'''
Corrupted legacy content below is intentionally ignored.

# Generated manually by ChatGPT
# Django
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations, models
from django.db.models import Q

"""
Legacy content below is intentionally ignored due to corruption.



BLUEPRINT_CORP_SCOPE_INDEX = models.Index(
    fields=["owner_kind", "corporation_id", "type_id"],
    name="indy_hub_bl_corp_scope_idx",
)
INDUSTRY_CORP_SCOPE_INDEX = models.Index(
    fields=["owner_kind", "corporation_id", "status"],
    name="indy_hub_in_corp_scope_idx",
)


def populate_owner_kind(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    Blueprint.objects.filter(owner_kind="").update(owner_kind="character")
    Blueprint.objects.filter(owner_kind__isnull=True).update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind="").update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind__isnull=True).update(owner_kind="character")


def purge_default_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(content_type__app_label="indy_hub").filter(
        Q(codename__startswith="add_")
        | Q(codename__startswith="change_")
        | Q(codename__startswith="delete_")
        | Q(codename__startswith="view_")
    ).delete()


def ensure_custom_permissions(apps, schema_editor):
    app_config = global_apps.get_app_config("indy_hub")
    create_permissions(app_config, verbosity=0)


def _index_exists(schema_editor, table_name: str, index_name: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table_name
        )
    return index_name in constraints


def _add_index_if_missing(model, index, schema_editor):
    table = model._meta.db_table
    if not _index_exists(schema_editor, table, index.name):
        schema_editor.add_index(model, index)


def _remove_index_if_exists(model, index, schema_editor):
    table = model._meta.db_table
    if _index_exists(schema_editor, table, index.name):
        schema_editor.remove_index(model, index)


def _add_blueprint_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _add_index_if_missing(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


def _remove_blueprint_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _remove_index_if_exists(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


def _add_industry_corp_scope_index(apps, schema_editor):
    """
    Corrupted legacy content removed.
    """

    # Generated manually by ChatGPT
    # Django
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions
    from django.db import migrations, models
    from django.db.models import Q


    BLUEPRINT_CORP_SCOPE_INDEX = models.Index(
        fields=["owner_kind", "corporation_id", "type_id"],
        name="indy_hub_bl_corp_scope_idx",
    )
    INDUSTRY_CORP_SCOPE_INDEX = models.Index(
        fields=["owner_kind", "corporation_id", "status"],
        name="indy_hub_in_corp_scope_idx",
    )


    def populate_owner_kind(apps, schema_editor):
        Blueprint = apps.get_model("indy_hub", "Blueprint")
        IndustryJob = apps.get_model("indy_hub", "IndustryJob")

        Blueprint.objects.filter(owner_kind="").update(owner_kind="character")
        Blueprint.objects.filter(owner_kind__isnull=True).update(owner_kind="character")
        IndustryJob.objects.filter(owner_kind="").update(owner_kind="character")
        IndustryJob.objects.filter(owner_kind__isnull=True).update(owner_kind="character")


    def purge_default_permissions(apps, schema_editor):
        Permission = apps.get_model("auth", "Permission")
        Permission.objects.filter(content_type__app_label="indy_hub").filter(
            Q(codename__startswith="add_")
            | Q(codename__startswith="change_")
            | Q(codename__startswith="delete_")
            | Q(codename__startswith="view_")
        ).delete()


    def ensure_custom_permissions(apps, schema_editor):
        app_config = global_apps.get_app_config("indy_hub")
        create_permissions(app_config, verbosity=0)


    def _index_exists(schema_editor, table_name: str, index_name: str) -> bool:
        with schema_editor.connection.cursor() as cursor:
            constraints = schema_editor.connection.introspection.get_constraints(
                cursor, table_name
            )
        return index_name in constraints


    def _add_index_if_missing(model, index, schema_editor):
        table = model._meta.db_table
        if not _index_exists(schema_editor, table, index.name):
            schema_editor.add_index(model, index)


    def _remove_index_if_exists(model, index, schema_editor):
        table = model._meta.db_table
        if _index_exists(schema_editor, table, index.name):
            schema_editor.remove_index(model, index)


    def _add_blueprint_corp_scope_index(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _add_index_if_missing(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


    def _remove_blueprint_corp_scope_index(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _remove_index_if_exists(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


    def _add_industry_corp_scope_index(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _add_index_if_missing(model, INDUSTRY_CORP_SCOPE_INDEX, schema_editor)


    def _remove_industry_corp_scope_index(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _remove_index_if_exists(model, INDUSTRY_CORP_SCOPE_INDEX, schema_editor)


    def _get_column_names(schema_editor, table_name: str) -> set[str]:
        with schema_editor.connection.cursor() as cursor:
            return {
                column.name
                for column in schema_editor.connection.introspection.get_table_description(
                    cursor, table_name
                )
            }


    def cleanup_corporation_assets_on_rollback(apps, schema_editor):
        Blueprint = apps.get_model("indy_hub", "Blueprint")
        IndustryJob = apps.get_model("indy_hub", "IndustryJob")

        blueprint_columns = _get_column_names(schema_editor, Blueprint._meta.db_table)
        industry_columns = _get_column_names(schema_editor, IndustryJob._meta.db_table)

        blueprint_filters = Q()
        if "owner_kind" in blueprint_columns:
            blueprint_filters |= Q(owner_kind="corporation")
        if "character_id" in blueprint_columns:
            blueprint_filters |= Q(character_id__isnull=True)

        industry_filters = Q()
        if "owner_kind" in industry_columns:
            industry_filters |= Q(owner_kind="corporation")
        if "character_id" in industry_columns:
            industry_filters |= Q(character_id__isnull=True)

        if blueprint_filters:
            Blueprint.objects.filter(blueprint_filters).delete()
        if industry_filters:
            IndustryJob.objects.filter(industry_filters).delete()


    def _column_exists(schema_editor, table_name: str, column_name: str) -> bool:
        with schema_editor.connection.cursor() as cursor:
            columns = schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        return any(column.name == column_name for column in columns)


    def _build_field(model, field_name: str):
        try:
            return model._meta.get_field(field_name)
        except Exception:
            pass

        if model._meta.model_name == "blueprint":
            field_map = {
                "corporation_id": models.BigIntegerField(blank=True, null=True),
                "corporation_name": models.CharField(blank=True, max_length=255),
                "owner_kind": models.CharField(
                    choices=[
                        ("character", "Character-owned"),
                        ("corporation", "Corporation-owned"),
                    ],
                    default="character",
                    max_length=16,
                ),
            }
        elif model._meta.model_name == "industryjob":
            field_map = {
                "corporation_id": models.BigIntegerField(blank=True, null=True),
                "corporation_name": models.CharField(blank=True, max_length=255),
                "owner_kind": models.CharField(
                    choices=[
                        ("character", "Character-owned"),
                        ("corporation", "Corporation-owned"),
                    ],
                    default="character",
                    max_length=16,
                ),
            }
        else:
            field_map = {}

        field = field_map.get(field_name)
        if field is None:
            return None
        field.set_attributes_from_name(field_name)
        field.model = model
        return field


    def _add_field_if_missing(model, field_name: str, schema_editor):
        table = model._meta.db_table
        if not _column_exists(schema_editor, table, field_name):
            field = _build_field(model, field_name)
            if field is None:
                return
            schema_editor.add_field(model, field)


    def _remove_field_if_exists(model, field_name: str, schema_editor):
        table = model._meta.db_table
        if not _column_exists(schema_editor, table, field_name):
            return
        try:
            field = model._meta.get_field(field_name)
        except Exception:
            return
        schema_editor.remove_field(model, field)


    def _add_blueprint_corporation_id(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _add_field_if_missing(model, "corporation_id", schema_editor)


    def _remove_blueprint_corporation_id(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _remove_field_if_exists(model, "corporation_id", schema_editor)


    def _add_blueprint_corporation_name(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _add_field_if_missing(model, "corporation_name", schema_editor)


    def _remove_blueprint_corporation_name(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _remove_field_if_exists(model, "corporation_name", schema_editor)


    def _add_blueprint_owner_kind(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _add_field_if_missing(model, "owner_kind", schema_editor)


    def _remove_blueprint_owner_kind(apps, schema_editor):
        model = apps.get_model("indy_hub", "Blueprint")
        _remove_field_if_exists(model, "owner_kind", schema_editor)


    def _add_industryjob_corporation_id(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _add_field_if_missing(model, "corporation_id", schema_editor)


    def _remove_industryjob_corporation_id(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _remove_field_if_exists(model, "corporation_id", schema_editor)


    def _add_industryjob_corporation_name(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _add_field_if_missing(model, "corporation_name", schema_editor)


    def _remove_industryjob_corporation_name(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _remove_field_if_exists(model, "corporation_name", schema_editor)


    def _add_industryjob_owner_kind(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _add_field_if_missing(model, "owner_kind", schema_editor)


    def _remove_industryjob_owner_kind(apps, schema_editor):
        model = apps.get_model("indy_hub", "IndustryJob")
        _remove_field_if_exists(model, "owner_kind", schema_editor)


    class Migration(migrations.Migration):

        dependencies = [
            ("indy_hub", "0029_alter_useronboardingprogress_options"),
        ]

        operations = [
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_blueprint_corporation_id,
                        reverse_code=_remove_blueprint_corporation_id,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="blueprint",
                        name="corporation_id",
                        field=models.BigIntegerField(blank=True, null=True),
                    )
                ],
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_blueprint_corporation_name,
                        reverse_code=_remove_blueprint_corporation_name,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="blueprint",
                        name="corporation_name",
                        field=models.CharField(blank=True, max_length=255),
                    )
                ],
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_blueprint_owner_kind,
                        reverse_code=_remove_blueprint_owner_kind,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="blueprint",
                        name="owner_kind",
                        field=models.CharField(
                            choices=[
                                ("character", "Character-owned"),
                                ("corporation", "Corporation-owned"),
                            ],
                            default="character",
                            max_length=16,
                        ),
                        preserve_default=False,
                    )
                ],
            ),
            migrations.AlterField(
                model_name="blueprint",
                name="character_id",
                field=models.BigIntegerField(blank=True, null=True),
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_industryjob_corporation_id,
                        reverse_code=_remove_industryjob_corporation_id,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="industryjob",
                        name="corporation_id",
                        field=models.BigIntegerField(blank=True, null=True),
                    )
                ],
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_industryjob_corporation_name,
                        reverse_code=_remove_industryjob_corporation_name,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="industryjob",
                        name="corporation_name",
                        field=models.CharField(blank=True, max_length=255),
                    )
                ],
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_industryjob_owner_kind,
                        reverse_code=_remove_industryjob_owner_kind,
                    )
                ],
                state_operations=[
                    migrations.AddField(
                        model_name="industryjob",
                        name="owner_kind",
                        field=models.CharField(
                            choices=[
                                ("character", "Character-owned"),
                                ("corporation", "Corporation-owned"),
                            ],
                            default="character",
                            max_length=16,
                        ),
                        preserve_default=False,
                    )
                ],
            ),
            migrations.AlterField(
                model_name="industryjob",
                name="character_id",
                field=models.BigIntegerField(blank=True, null=True),
            ),
            migrations.AlterModelOptions(
                name="blueprint",
                options={
                    "db_table": "indy_hub_indyblueprint",
                    "default_permissions": (),
                    "permissions": (
                        ("can_access_indy_hub", "Can access Indy Hub module"),
                        (
                            "can_manage_copy_requests",
                            "Can request or share blueprint copies",
                        ),
                        (
                            "can_manage_corporate_assets",
                            "Can manage corporation blueprints and jobs",
                        ),
                    ),
                    "verbose_name": "Blueprint",
                    "verbose_name_plural": "Blueprints",
                },
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_blueprint_corp_scope_index,
                        reverse_code=_remove_blueprint_corp_scope_index,
                    )
                ],
                state_operations=[
                    migrations.AddIndex(
                        model_name="blueprint",
                        index=BLUEPRINT_CORP_SCOPE_INDEX,
                    )
                ],
            ),
            migrations.SeparateDatabaseAndState(
                database_operations=[
                    migrations.RunPython(
                        _add_industry_corp_scope_index,
                        reverse_code=_remove_industry_corp_scope_index,
                    )
                ],
                state_operations=[
                    migrations.AddIndex(
                        model_name="industryjob",
                        index=INDUSTRY_CORP_SCOPE_INDEX,
                    )
                ],
            ),
            migrations.RunPython(populate_owner_kind, migrations.RunPython.noop),
            migrations.RunPython(purge_default_permissions, migrations.RunPython.noop),
            migrations.RunPython(ensure_custom_permissions, migrations.RunPython.noop),
            migrations.RunPython(
                migrations.RunPython.noop,
                cleanup_corporation_assets_on_rollback,
            ),
        ]
                    _add_industryjob_owner_kind,
                    reverse_code=_remove_industryjob_owner_kind,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="owner_kind",
                    field=models.CharField(
                        choices=[
                            ("character", "Character-owned"),
                            ("corporation", "Corporation-owned"),
                        ],
                        default="character",
                        max_length=16,
                    ),
                    preserve_default=False,
                )
            ],
        ),
        migrations.AlterField(
            model_name="industryjob",
            name="character_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="blueprint",
            options={
                "db_table": "indy_hub_indyblueprint",
                "default_permissions": (),
                "permissions": (
                    ("can_access_indy_hub", "Can access Indy Hub module"),
                    (
                        "can_manage_copy_requests",
                        "Can request or share blueprint copies",
                    ),
                    (
                        "can_manage_corporate_assets",
                        "Can manage corporation blueprints and jobs",
                    ),
                ),
                "verbose_name": "Blueprint",
                "verbose_name_plural": "Blueprints",
            },
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_blueprint_corp_scope_index,
                    reverse_code=_remove_blueprint_corp_scope_index,
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="blueprint",
                    index=BLUEPRINT_CORP_SCOPE_INDEX,
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_industry_corp_scope_index,
                    reverse_code=_remove_industry_corp_scope_index,
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="industryjob",
                    index=INDUSTRY_CORP_SCOPE_INDEX,
                )
            ],
        ),
        migrations.RunPython(populate_owner_kind, migrations.RunPython.noop),
        migrations.RunPython(purge_default_permissions, migrations.RunPython.noop),
        migrations.RunPython(ensure_custom_permissions, migrations.RunPython.noop),
        migrations.RunPython(
            migrations.RunPython.noop,
            cleanup_corporation_assets_on_rollback,
        ),
    ]
                                migrations.SeparateDatabaseAndState(
                                    database_operations=[
                                        migrations.RunPython(
                                            _add_blueprint_corporation_name,
                                            reverse_code=_remove_blueprint_corporation_name,
                                        )
                                    ],
                                    state_operations=[
                                        migrations.AddField(
                                            model_name="blueprint",
                                            name="corporation_name",
                                            field=models.CharField(blank=True, max_length=255),
                                        )
                                    ],
                                ),
                                migrations.SeparateDatabaseAndState(
                                    database_operations=[
                                        migrations.RunPython(
                                            _add_blueprint_owner_kind,
                                            reverse_code=_remove_blueprint_owner_kind,
                                        )
                                    ],
                                    state_operations=[
                                        migrations.AddField(
                                            model_name="blueprint",
                                            name="owner_kind",
                                            field=models.CharField(
                                                choices=[
                                                    ("character", "Character-owned"),
                                                    ("corporation", "Corporation-owned"),
                                                ],
                                                default="character",
                                                max_length=16,
                                            ),
                                            preserve_default=False,
                                        )
                                    ],
                                ),

'''

# Generated manually by ChatGPT
# Django
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations, models
from django.db.models import Q

BLUEPRINT_CORP_SCOPE_INDEX = models.Index(
    fields=["owner_kind", "corporation_id", "type_id"],
    name="indy_hub_bl_corp_scope_idx",
)
INDUSTRY_CORP_SCOPE_INDEX = models.Index(
    fields=["owner_kind", "corporation_id", "status"],
    name="indy_hub_in_corp_scope_idx",
)


def populate_owner_kind(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    Blueprint.objects.filter(owner_kind="").update(owner_kind="character")
    Blueprint.objects.filter(owner_kind__isnull=True).update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind="").update(owner_kind="character")
    IndustryJob.objects.filter(owner_kind__isnull=True).update(owner_kind="character")


def purge_default_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(content_type__app_label="indy_hub").filter(
        Q(codename__startswith="add_")
        | Q(codename__startswith="change_")
        | Q(codename__startswith="delete_")
        | Q(codename__startswith="view_")
    ).delete()


def ensure_custom_permissions(apps, schema_editor):
    app_config = global_apps.get_app_config("indy_hub")
    create_permissions(app_config, verbosity=0)


def _index_exists(schema_editor, table_name: str, index_name: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table_name
        )
    return index_name in constraints


def _add_index_if_missing(model, index, schema_editor):
    table = model._meta.db_table
    if not _index_exists(schema_editor, table, index.name):
        schema_editor.add_index(model, index)


def _remove_index_if_exists(model, index, schema_editor):
    table = model._meta.db_table
    if _index_exists(schema_editor, table, index.name):
        schema_editor.remove_index(model, index)


def _add_blueprint_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _add_index_if_missing(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


def _remove_blueprint_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _remove_index_if_exists(model, BLUEPRINT_CORP_SCOPE_INDEX, schema_editor)


def _add_industry_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _add_index_if_missing(model, INDUSTRY_CORP_SCOPE_INDEX, schema_editor)


def _remove_industry_corp_scope_index(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _remove_index_if_exists(model, INDUSTRY_CORP_SCOPE_INDEX, schema_editor)


def _get_column_names(schema_editor, table_name: str) -> set[str]:
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }


def cleanup_corporation_assets_on_rollback(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    blueprint_columns = _get_column_names(schema_editor, Blueprint._meta.db_table)
    industry_columns = _get_column_names(schema_editor, IndustryJob._meta.db_table)

    blueprint_filters = Q()
    if "owner_kind" in blueprint_columns:
        blueprint_filters |= Q(owner_kind="corporation")
    if "character_id" in blueprint_columns:
        blueprint_filters |= Q(character_id__isnull=True)

    industry_filters = Q()
    if "owner_kind" in industry_columns:
        industry_filters |= Q(owner_kind="corporation")
    if "character_id" in industry_columns:
        industry_filters |= Q(character_id__isnull=True)

    if blueprint_filters:
        Blueprint.objects.filter(blueprint_filters).delete()
    if industry_filters:
        IndustryJob.objects.filter(industry_filters).delete()


def _column_exists(schema_editor, table_name: str, column_name: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(
            cursor, table_name
        )
    return any(column.name == column_name for column in columns)


def _build_field(model, field_name: str):
    try:
        return model._meta.get_field(field_name)
    except Exception:
        pass

    if model._meta.model_name == "blueprint":
        field_map = {
            "corporation_id": models.BigIntegerField(blank=True, null=True),
            "corporation_name": models.CharField(blank=True, max_length=255),
            "owner_kind": models.CharField(
                choices=[
                    ("character", "Character-owned"),
                    ("corporation", "Corporation-owned"),
                ],
                default="character",
                max_length=16,
            ),
        }
    elif model._meta.model_name == "industryjob":
        field_map = {
            "corporation_id": models.BigIntegerField(blank=True, null=True),
            "corporation_name": models.CharField(blank=True, max_length=255),
            "owner_kind": models.CharField(
                choices=[
                    ("character", "Character-owned"),
                    ("corporation", "Corporation-owned"),
                ],
                default="character",
                max_length=16,
            ),
        }
    else:
        field_map = {}

    field = field_map.get(field_name)
    if field is None:
        return None
    field.set_attributes_from_name(field_name)
    field.model = model
    return field


def _add_field_if_missing(model, field_name: str, schema_editor):
    table = model._meta.db_table
    if not _column_exists(schema_editor, table, field_name):
        field = _build_field(model, field_name)
        if field is None:
            return
        schema_editor.add_field(model, field)


def _remove_field_if_exists(model, field_name: str, schema_editor):
    table = model._meta.db_table
    if not _column_exists(schema_editor, table, field_name):
        return
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return
    schema_editor.remove_field(model, field)


def _add_blueprint_corporation_id(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _add_field_if_missing(model, "corporation_id", schema_editor)


def _remove_blueprint_corporation_id(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _remove_field_if_exists(model, "corporation_id", schema_editor)


def _add_blueprint_corporation_name(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _add_field_if_missing(model, "corporation_name", schema_editor)


def _remove_blueprint_corporation_name(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _remove_field_if_exists(model, "corporation_name", schema_editor)


def _add_blueprint_owner_kind(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _add_field_if_missing(model, "owner_kind", schema_editor)


def _remove_blueprint_owner_kind(apps, schema_editor):
    model = apps.get_model("indy_hub", "Blueprint")
    _remove_field_if_exists(model, "owner_kind", schema_editor)


def _add_industryjob_corporation_id(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _add_field_if_missing(model, "corporation_id", schema_editor)


def _remove_industryjob_corporation_id(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _remove_field_if_exists(model, "corporation_id", schema_editor)


def _add_industryjob_corporation_name(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _add_field_if_missing(model, "corporation_name", schema_editor)


def _remove_industryjob_corporation_name(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _remove_field_if_exists(model, "corporation_name", schema_editor)


def _add_industryjob_owner_kind(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _add_field_if_missing(model, "owner_kind", schema_editor)


def _remove_industryjob_owner_kind(apps, schema_editor):
    model = apps.get_model("indy_hub", "IndustryJob")
    _remove_field_if_exists(model, "owner_kind", schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0029_alter_useronboardingprogress_options"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_blueprint_corporation_id,
                    reverse_code=_remove_blueprint_corporation_id,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="blueprint",
                    name="corporation_id",
                    field=models.BigIntegerField(blank=True, null=True),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_blueprint_corporation_name,
                    reverse_code=_remove_blueprint_corporation_name,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="blueprint",
                    name="corporation_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_blueprint_owner_kind,
                    reverse_code=_remove_blueprint_owner_kind,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="blueprint",
                    name="owner_kind",
                    field=models.CharField(
                        choices=[
                            ("character", "Character-owned"),
                            ("corporation", "Corporation-owned"),
                        ],
                        default="character",
                        max_length=16,
                    ),
                    preserve_default=False,
                )
            ],
        ),
        migrations.AlterField(
            model_name="blueprint",
            name="character_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_industryjob_corporation_id,
                    reverse_code=_remove_industryjob_corporation_id,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="corporation_id",
                    field=models.BigIntegerField(blank=True, null=True),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_industryjob_corporation_name,
                    reverse_code=_remove_industryjob_corporation_name,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="corporation_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_industryjob_owner_kind,
                    reverse_code=_remove_industryjob_owner_kind,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="owner_kind",
                    field=models.CharField(
                        choices=[
                            ("character", "Character-owned"),
                            ("corporation", "Corporation-owned"),
                        ],
                        default="character",
                        max_length=16,
                    ),
                    preserve_default=False,
                )
            ],
        ),
        migrations.AlterField(
            model_name="industryjob",
            name="character_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="blueprint",
            options={
                "db_table": "indy_hub_indyblueprint",
                "default_permissions": (),
                "permissions": (
                    ("can_access_indy_hub", "Can access Indy Hub module"),
                    (
                        "can_manage_copy_requests",
                        "Can request or share blueprint copies",
                    ),
                    (
                        "can_manage_corporate_assets",
                        "Can manage corporation blueprints and jobs",
                    ),
                ),
                "verbose_name": "Blueprint",
                "verbose_name_plural": "Blueprints",
            },
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_blueprint_corp_scope_index,
                    reverse_code=_remove_blueprint_corp_scope_index,
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="blueprint",
                    index=BLUEPRINT_CORP_SCOPE_INDEX,
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _add_industry_corp_scope_index,
                    reverse_code=_remove_industry_corp_scope_index,
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="industryjob",
                    index=INDUSTRY_CORP_SCOPE_INDEX,
                )
            ],
        ),
        migrations.RunPython(populate_owner_kind, migrations.RunPython.noop),
        migrations.RunPython(purge_default_permissions, migrations.RunPython.noop),
        migrations.RunPython(ensure_custom_permissions, migrations.RunPython.noop),
        migrations.RunPython(
            migrations.RunPython.noop,
            cleanup_corporation_assets_on_rollback,
        ),
    ]
