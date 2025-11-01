"""Helpers to build rich notifications for industry jobs."""

from __future__ import annotations

# Standard Library
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Django
from django.utils.translation import gettext_lazy as _

from .eve import get_character_name

if TYPE_CHECKING:
    # AA Example App
    from indy_hub.models import Blueprint


@dataclass(frozen=True)
class JobNotificationPayload:
    """Structured notification content for a completed industry job."""

    title: str
    message: str
    thumbnail_url: str | None = None


def build_job_notification_payload(job, *, blueprint=None) -> JobNotificationPayload:
    """Return a formatted notification payload for the given industry job.

    Args:
        job: An :class:`~indy_hub.models.IndustryJob` instance (saved or unsaved).
        blueprint: Optional blueprint instance associated to the job. When omitted,
            the helper attempts to resolve it automatically.
    """

    character_name = _resolve_character_name(job)
    blueprint_obj = blueprint or _resolve_blueprint(job)
    blueprint_name = _resolve_blueprint_name(job, blueprint_obj)
    activity_label = _resolve_activity_label(job)
    result_line = _resolve_result(job, blueprint_obj)
    location_label = _resolve_location(job)
    thumbnail_url = _resolve_image_url(job, blueprint_obj)

    title = _("%(character)s - Job #%(job_id)s completed") % {
        "character": character_name,
        "job_id": getattr(job, "job_id", "?"),
    }

    lines: list[str] = [
        _("Character: %(name)s") % {"name": character_name},
        _("Job: #%(job_id)s") % {"job_id": getattr(job, "job_id", "?")},
        _("Blueprint: %(name)s") % {"name": blueprint_name},
        _("Activity: %(activity)s") % {"activity": activity_label},
    ]

    if result_line:
        lines.append(_("Result: %(result)s") % {"result": result_line})

    lines.append(_("Location: %(location)s") % {"location": location_label})

    if thumbnail_url:
        lines.append(_("Image preview: %(url)s") % {"url": thumbnail_url})

    message = "\n".join(lines)

    return JobNotificationPayload(
        title=title,
        message=message,
        thumbnail_url=thumbnail_url,
    )


def _resolve_character_name(job) -> str:
    explicit_name = getattr(job, "character_name", None)
    if explicit_name:
        return explicit_name

    for field in ("character_id", "installer_id"):
        identifier = getattr(job, field, None)
        if identifier:
            name = get_character_name(identifier)
            if name:
                return name

    owner = getattr(job, "owner_user", None)
    if owner and getattr(owner, "username", None):
        return owner.username

    return _("Unknown pilot")


def _resolve_blueprint(job) -> Blueprint | None:
    blueprint_id = getattr(job, "blueprint_id", None)
    blueprint_type_id = getattr(job, "blueprint_type_id", None)
    owner = getattr(job, "owner_user", None)
    owner_kind = getattr(job, "owner_kind", None)

    if not owner:
        return None

    # AA Example App
    from indy_hub.models import Blueprint

    query = Blueprint.objects.filter(owner_user=owner)
    if owner_kind:
        query = query.filter(owner_kind=owner_kind)

    if blueprint_id:
        candidate = (
            query.filter(blueprint_id=blueprint_id).order_by("-updated_at").first()
        )
        if candidate:
            return candidate

    if blueprint_type_id:
        return query.filter(type_id=blueprint_type_id).order_by("-updated_at").first()

    return None


def _resolve_blueprint_name(job, blueprint) -> str:
    if getattr(job, "blueprint_type_name", None):
        return job.blueprint_type_name
    if blueprint and getattr(blueprint, "type_name", None):
        return blueprint.type_name
    blueprint_type_id = getattr(job, "blueprint_type_id", None)
    if blueprint_type_id:
        return str(blueprint_type_id)
    return _("Unknown blueprint")


_ACTIVITY_LABELS = {
    1: _("Manufacturing"),
    3: _("Time Efficiency Research"),
    4: _("Material Efficiency Research"),
    5: _("Copying"),
    7: _("Reverse Engineering"),
    8: _("Invention"),
    9: _("Reactions"),
    11: _("Reactions"),
}


def _resolve_activity_label(job) -> str:
    label = getattr(job, "activity_name", None)
    if label:
        return label
    activity_id = getattr(job, "activity_id", None)
    if activity_id in _ACTIVITY_LABELS:
        return _ACTIVITY_LABELS[activity_id]
    return _("Industry job")


def _resolve_result(job, blueprint) -> str | None:
    activity_id = getattr(job, "activity_id", None)
    runs = _coalesce(getattr(job, "successful_runs", None), getattr(job, "runs", None))

    if activity_id == 3:  # Time Efficiency Research
        return _describe_efficiency_result(
            current=getattr(blueprint, "time_efficiency", None),
            increment=runs,
            label="TE",
        )
    if activity_id == 4:  # Material Efficiency Research
        return _describe_efficiency_result(
            current=getattr(blueprint, "material_efficiency", None),
            increment=runs,
            label="ME",
        )
    if activity_id == 5:  # Copying
        if runs:
            licensed = getattr(job, "licensed_runs", None)
            if licensed:
                return _("Copies: %(copies)s (runs: %(runs)s)") % {
                    "copies": runs,
                    "runs": licensed,
                }
            return _("Copies: %(copies)s") % {"copies": runs}
        return None
    if activity_id == 1:  # Manufacturing
        product_name = getattr(job, "product_type_name", None) or _("Unknown product")
        if runs:
            return _("Product: %(name)s (qty %(qty)s)") % {
                "name": product_name,
                "qty": runs,
            }
        return _("Product: %(name)s") % {"name": product_name}
    if runs:
        return _("Completed runs: %(count)s") % {"count": runs}
    return None


def _describe_efficiency_result(
    *, current: int | None, increment: int | None, label: str
) -> str | None:
    if increment in (None, 0):
        if current is not None:
            return f"{label} {current}"
        return None

    if current is None:
        return f"{label} +{increment}"

    previous = max(0, current - increment)
    return f"{label} {previous} -> {current}"


def _resolve_location(job) -> str:
    location = getattr(job, "location_name", None)
    if location:
        return location
    return _("Unknown location")


def _resolve_image_url(job, blueprint) -> str | None:
    activity_id = getattr(job, "activity_id", None)

    def _blueprint_type_id() -> int | None:
        for attr in ("blueprint_type_id",):
            value = getattr(job, attr, None)
            if value:
                return value
        if blueprint is not None and getattr(blueprint, "type_id", None):
            return blueprint.type_id
        return None

    def _product_type_id() -> int | None:
        for attr in ("product_type_id", "blueprint_type_id"):
            value = getattr(job, attr, None)
            if value:
                return value
        if blueprint is not None:
            for attr in ("product_type_id", "type_id"):
                value = getattr(blueprint, attr, None)
                if value:
                    return value
        return None

    if activity_id in {3, 4}:  # TE / ME research
        type_id = _blueprint_type_id()
        suffix = "bp"
    elif activity_id == 5:  # Copying
        type_id = _blueprint_type_id()
        suffix = "bpc"
    else:  # Manufacturing, reactions, or other
        type_id = _product_type_id()
        suffix = "icon"

    if not type_id:
        return None

    return f"https://images.evetech.net/types/{type_id}/{suffix}"


def _coalesce(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None
