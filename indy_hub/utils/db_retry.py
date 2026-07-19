"""Database retry helpers for MySQL concurrency edge cases."""

from __future__ import annotations

# Standard Library
import random
import time
from typing import Any

# Django
from django.db import IntegrityError, transaction
from django.db.utils import OperationalError


def _is_mysql_deadlock_error(exc: Exception) -> bool:
    if getattr(exc, "args", None):
        code = exc.args[0]
        if code == 1213:
            return True
    return "Deadlock found" in str(exc)


def _is_mysql_duplicate_key_error(exc: Exception) -> bool:
    if getattr(exc, "args", None):
        code = exc.args[0]
        if code == 1062:
            return True
    return "Duplicate entry" in str(exc)


def _model_auto_now_field_names(model) -> list[str]:
    return [
        field.attname
        for field in model._meta.concrete_fields
        if getattr(field, "auto_now", False)
    ]


def update_or_create_with_mysql_retry(
    model,
    *,
    lookup: dict[str, object],
    defaults: dict[str, object],
    max_attempts: int = 3,
    logger: Any | None = None,
) -> tuple[object, bool]:
    """Run `update_or_create` with retries for MySQL deadlocks and duplicate keys."""

    def _log(message: str, *args: object) -> None:
        if logger is not None:
            logger.warning(message, *args)

    def _retry_delay(attempt: int) -> float:
        return 0.2 * attempt + random.random() * 0.2

    for attempt in range(1, max_attempts + 1):
        try:
            return model.objects.update_or_create(**lookup, defaults=defaults)
        except OperationalError as exc:
            if not _is_mysql_deadlock_error(exc) or attempt >= max_attempts:
                raise
            delay = _retry_delay(attempt)
            _log(
                "Deadlock while writing %s; retrying (%s/%s) in %.2fs",
                model.__name__,
                attempt,
                max_attempts,
                delay,
            )
            time.sleep(delay)
            continue
        except IntegrityError as exc:
            if not _is_mysql_duplicate_key_error(exc):
                raise
            try:
                with transaction.atomic():
                    instance = model.objects.select_for_update().get(**lookup)
                    for field_name, value in defaults.items():
                        setattr(instance, field_name, value)
                    if defaults:
                        update_fields = list(defaults.keys())
                        for field_name in _model_auto_now_field_names(model):
                            if field_name not in update_fields:
                                update_fields.append(field_name)
                        instance.save(update_fields=update_fields)
                    _log(
                        "Duplicate key while writing %s; refreshed existing row (%s/%s)",
                        model.__name__,
                        attempt,
                        max_attempts,
                    )
                    return instance, False
            except OperationalError as recovery_exc:
                if (
                    not _is_mysql_deadlock_error(recovery_exc)
                    or attempt >= max_attempts
                ):
                    raise
                delay = _retry_delay(attempt)
                _log(
                    "Deadlock while recovering duplicate key for %s; retrying (%s/%s) in %.2fs",
                    model.__name__,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            except model.DoesNotExist:
                if attempt >= max_attempts:
                    raise exc
                delay = _retry_delay(attempt)
                _log(
                    "Duplicate key while writing %s but row was not visible yet; retrying (%s/%s) in %.2fs",
                    model.__name__,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)

    raise RuntimeError("Unreachable: MySQL retry loop exhausted")
