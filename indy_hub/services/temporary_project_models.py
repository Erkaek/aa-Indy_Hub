"""Lightweight temporary project data structures."""

from __future__ import annotations

# Standard Library
from collections.abc import Sequence
from dataclasses import dataclass

# AA Example App
from ..models import ProductionProjectItem


class TemporaryItemsQuerySet:
    def __init__(self, items: Sequence[ProductionProjectItem]):
        self._items = list(items)

    def filter(self, **kwargs):
        items = self._items
        for field_name, expected_value in kwargs.items():
            items = [
                item for item in items if getattr(item, field_name) == expected_value
            ]
        return TemporaryItemsQuerySet(items)

    def exclude(self, **kwargs):
        items = self._items
        for field_name, expected_value in kwargs.items():
            items = [
                item for item in items if getattr(item, field_name) != expected_value
            ]
        return TemporaryItemsQuerySet(items)

    def order_by(self, *fields):
        items = list(self._items)
        for field_name in reversed(fields):
            reverse = field_name.startswith("-")
            normalized_name = field_name[1:] if reverse else field_name
            items.sort(
                key=lambda item: getattr(item, normalized_name, None), reverse=reverse
            )
        return TemporaryItemsQuerySet(items)

    def __iter__(self):
        return iter(self._items)


@dataclass
class TemporaryProductionProject:
    user: object
    name: str
    status: str
    source_kind: str
    source_text: str
    source_name: str
    notes: str
    workspace_state: dict[str, object]
    project_ref: str
    temp_project_ref: str
    items: TemporaryItemsQuerySet
    id: int | None = None