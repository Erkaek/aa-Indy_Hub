"""Offer and negotiation helper functions for industry blueprint chats."""

from __future__ import annotations

# Standard Library
import re
from decimal import Decimal, InvalidOperation

from ..models import BlueprintCopyMessage

NEGOTIATION_BAR_MESSAGE_RE = re.compile(
    r"^(Buyer|Builder) (proposed|counter-proposed|reconfirmed) [\d,]+(?:\.\d{2})? ISK\.$"
)


def normalize_offer_amount(raw_amount) -> Decimal | None:
    if raw_amount in {None, ""}:
        return None

    try:
        amount = Decimal(str(raw_amount).strip().replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if amount <= 0:
        return None

    return amount.quantize(Decimal("0.01"))


def format_isk_amount(amount: Decimal | None) -> str:
    if amount is None:
        return ""

    normalized = amount.quantize(Decimal("0.01"))
    whole_amount = normalized.quantize(Decimal("1"))
    if normalized == whole_amount:
        return f"{int(whole_amount):,}"
    return f"{normalized:,.2f}"


def format_percent_compact(value: Decimal | int | float | str | None) -> str:
    try:
        numeric_value = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        numeric_value = Decimal("0.00")
    return format(numeric_value, "f").rstrip("0").rstrip(".") or "0"


def format_duration_compact(total_seconds: int | float | Decimal | None) -> str:
    seconds = max(0, int(total_seconds or 0))
    if seconds <= 0:
        return "-"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts: list[str] = []

    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def classify_bp_chat_message(message: BlueprintCopyMessage) -> str:
    content = (message.content or "").strip()
    if message.sender_role == BlueprintCopyMessage.SenderRole.SYSTEM:
        return "proposal" if NEGOTIATION_BAR_MESSAGE_RE.match(content) else "system"
    if NEGOTIATION_BAR_MESSAGE_RE.match(content):
        return "proposal"
    return "message"
