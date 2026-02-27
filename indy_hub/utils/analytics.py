"""Safe helpers for Alliance Auth analytics integration."""

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


def emit_analytics_event(
    *,
    task: str,
    label: str = "",
    result: str = "",
    value: int = 1,
    event_type: str = "Celery",
    namespace: str = "indy_hub",
) -> None:
    """Emit an Alliance Auth analytics event when available.

    This helper is intentionally fail-safe: if analytics is disabled,
    unavailable, or errors, app logic must continue unaffected.
    """
    try:
        # Alliance Auth
        from allianceauth.analytics.tasks import analytics_event
    except Exception:
        return

    try:
        analytics_event(
            namespace=namespace,
            task=task,
            label=label or "",
            result=result or "",
            value=int(value),
            event_type=event_type,
        )
    except Exception as exc:
        logger.debug("Analytics event emission failed (%s): %s", task, exc)


def emit_view_analytics_event(
    *,
    view_name: str,
    request=None,
    result: str = "success",
    namespace: str = "indy_hub",
) -> None:
    """Emit a standardized analytics event for an Indy Hub view/API hit."""

    method = ""
    if request is not None:
        try:
            method = str(getattr(request, "method", "") or "").upper()
        except Exception:
            method = ""

    label = f"{view_name}:{method}" if method else view_name
    emit_analytics_event(
        task="view_hit",
        label=label,
        result=result,
        event_type="Stats",
        namespace=namespace,
    )
