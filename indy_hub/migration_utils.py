"""Utility helpers for safe index and constraint operations in migrations."""

from __future__ import annotations

# Django
from django.db import models


def _constraint_exists(schema_editor, table_name: str, constraint_name: str) -> bool:
    """Return True if the given constraint or index exists on the table."""

    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)

    normalized = constraint_name.lower()
    return any(name.lower() == normalized for name in constraints)


def add_index_if_missing(
    apps,
    schema_editor,
    *,
    app_label: str,
    model_name: str,
    index_name: str,
    fields: tuple[str, ...] | list[str],
) -> None:
    """Create the index only when it is absent on the target database."""

    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table

    if _constraint_exists(schema_editor, table_name, index_name):
        return

    index = models.Index(name=index_name, fields=list(fields))
    schema_editor.add_index(model, index)


def remove_index_if_exists(
    apps,
    schema_editor,
    *,
    app_label: str,
    model_name: str,
    index_name: str,
    fields: tuple[str, ...] | list[str],
) -> None:
    """Drop the index only when it exists."""

    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table

    if not _constraint_exists(schema_editor, table_name, index_name):
        return

    index = models.Index(name=index_name, fields=list(fields))
    schema_editor.remove_index(model, index)


def add_unique_constraint_if_missing(
    apps,
    schema_editor,
    *,
    app_label: str,
    model_name: str,
    constraint_name: str,
    fields: tuple[str, ...] | list[str],
) -> None:
    """Create the unique constraint only when absent."""

    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table

    if _constraint_exists(schema_editor, table_name, constraint_name):
        return

    constraint = models.UniqueConstraint(fields=list(fields), name=constraint_name)
    schema_editor.add_constraint(model, constraint)


def remove_unique_constraint_if_exists(
    apps,
    schema_editor,
    *,
    app_label: str,
    model_name: str,
    constraint_name: str,
    fields: tuple[str, ...] | list[str],
) -> None:
    """Drop the unique constraint only when present."""

    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table

    if not _constraint_exists(schema_editor, table_name, constraint_name):
        return

    constraint = models.UniqueConstraint(fields=list(fields), name=constraint_name)
    schema_editor.remove_constraint(model, constraint)
