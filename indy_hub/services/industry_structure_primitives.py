"""Primitive helpers for industry structure math and security-band logic."""

# Standard Library
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from ..models import IndustryStructure

ISK_QUANTUM = Decimal("1")


def round_isk(value: Decimal) -> Decimal:
    return value.quantize(ISK_QUANTUM, rounding=ROUND_CEILING)


def normalize_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def normalize_int(value: Decimal | int | float | str | None) -> int | None:
    normalized = normalize_decimal(value)
    if normalized == 0 and value in {None, ""}:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError, ArithmeticError):
        return None


def round_security_status(
    security_status: Decimal | int | float | str | None,
) -> Decimal:
    """Round security status to in-game display precision."""

    value = normalize_decimal(security_status)
    if value == Decimal("0"):
        return Decimal("0.0")
    if Decimal("0") < value < Decimal("0.05"):
        return Decimal("0.1")
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def security_status_to_band(security_status: Decimal | int | float | str | None) -> str:
    value = normalize_decimal(security_status)
    if value >= Decimal("0.45"):
        return IndustryStructure.SecurityBand.HIGHSEC
    if value > Decimal("0"):
        return IndustryStructure.SecurityBand.LOWSEC
    return IndustryStructure.SecurityBand.NULLSEC
