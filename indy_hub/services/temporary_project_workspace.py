"""Temporary production project workspace helpers."""

from __future__ import annotations

# Standard Library
import secrets
from collections.abc import Sequence

# Django
from django.core.cache import cache

from ..models import PROJECT_REF_BASE36_ALPHABET, ProductionProject

TEMP_PROJECT_CACHE_TIMEOUT_SECONDS = 60 * 60 * 24
TEMP_PROJECT_REF_LENGTH = 20


def generate_temporary_project_ref() -> str:
    return "".join(
        secrets.choice(PROJECT_REF_BASE36_ALPHABET)
        for _ in range(TEMP_PROJECT_REF_LENGTH)
    )


def temporary_project_cache_key(temp_project_ref: str) -> str:
    return f"indy_hub:temp_project:{str(temp_project_ref or '').strip().lower()}"


def create_temporary_project_workspace(
    *,
    user,
    name: str,
    status: str,
    source_kind: str,
    source_text: str,
    source_name: str,
    selected_entries: Sequence[dict[str, object]],
    workspace_state: dict[str, object] | None = None,
    notes: str = "",
) -> str:
    temp_project_ref = generate_temporary_project_ref()
    payload = {
        "user_id": int(user.id),
        "name": str(name or source_name or "New production project").strip()[:255],
        "status": str(status or ProductionProject.Status.DRAFT),
        "source_kind": str(source_kind or ProductionProject.SourceKind.MANUAL),
        "source_text": str(source_text or ""),
        "source_name": str(source_name or "").strip()[:255],
        "selected_entries": [dict(entry) for entry in selected_entries],
        "workspace_state": dict(workspace_state or {}),
        "notes": str(notes or ""),
    }
    cache.set(
        temporary_project_cache_key(temp_project_ref),
        payload,
        TEMP_PROJECT_CACHE_TIMEOUT_SECONDS,
    )
    return temp_project_ref


def get_temporary_project_workspace(
    user, temp_project_ref: str
) -> dict[str, object] | None:
    state = cache.get(temporary_project_cache_key(temp_project_ref))
    if not isinstance(state, dict):
        return None
    if int(state.get("user_id") or 0) != int(user.id):
        return None
    return state


def set_temporary_project_workspace(
    temp_project_ref: str,
    state: dict[str, object],
) -> None:
    cache.set(
        temporary_project_cache_key(temp_project_ref),
        state,
        TEMP_PROJECT_CACHE_TIMEOUT_SECONDS,
    )


def delete_temporary_project_workspace(temp_project_ref: str) -> None:
    cache.delete(temporary_project_cache_key(temp_project_ref))


def build_temporary_project_workspace_state(
    *,
    source_kind: str,
    source_name: str,
    fit_quantities: Sequence[dict[str, object]] | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {
        "simulation_name": str(source_name or ""),
        "simulationName": str(source_name or ""),
        "active_tab": "materials",
        "activeBlueprintTab": "materials",
        "runs": 1,
    }
    if str(source_kind) == ProductionProject.SourceKind.EFT and fit_quantities:
        state["finalOutputQuantities"] = [
            {
                "index": index,
                "fitGroup": str(entry.get("fitGroup") or ""),
                "label": str(entry.get("label") or ""),
                "quantity": max(1, int(entry.get("quantity") or 1)),
            }
            for index, entry in enumerate(fit_quantities)
            if isinstance(entry, dict)
        ]
    return state
