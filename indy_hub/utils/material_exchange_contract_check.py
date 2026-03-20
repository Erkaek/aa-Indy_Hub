from __future__ import annotations

# Standard Library
import re
from collections import Counter
from collections.abc import Iterable

CONTRACT_EXPORT_LABELS = [
    "Contract Type",
    "Description",
    "Availability",
    "Location",
    "Expiration",
    "Sales Tax",
    "Broker's Fee",
    "Deposit",
    "I will pay",
    "I will receive",
    "Items For Sale",
    "Items Required",
]

MULTILINE_LABELS = {"Items For Sale", "Items Required"}


def collapse_whitespace(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_text(value: str | None) -> str:
    return collapse_whitespace(value).casefold()


def parse_contract_export(raw_text: str) -> dict[str, str]:
    """Parse an in-game contract copy/paste export into labeled fields."""

    fields: dict[str, str] = {}
    current_label: str | None = None

    for raw_line in (raw_text or "").replace("\r", "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        if "\t" in raw_line:
            parts = [part.strip() for part in raw_line.split("\t")]
            label = parts[0]
            if label in CONTRACT_EXPORT_LABELS:
                value = " ".join(part for part in parts[1:] if part).strip()
                fields[label] = collapse_whitespace(value)
                current_label = label
                continue

        matched_label = next(
            (label for label in CONTRACT_EXPORT_LABELS if line.startswith(label)),
            None,
        )
        if matched_label is not None:
            value = line[len(matched_label) :].strip("\t :")
            fields[matched_label] = collapse_whitespace(value)
            current_label = matched_label
            continue

        if current_label in MULTILINE_LABELS:
            previous = fields.get(current_label, "")
            fields[current_label] = collapse_whitespace(f"{previous} {line}")

    return fields


def parse_isk_amount(raw_value: str | None) -> int | None:
    """Parse the first ISK amount from a copied contract line."""

    value = collapse_whitespace(raw_value)
    if not value:
        return None

    head = value.split("ISK", 1)[0]
    digits = re.sub(r"[^0-9]", "", head)
    if not digits:
        return None
    return int(digits)


def parse_contract_items(raw_value: str | None) -> tuple[Counter[str], dict[str, str]]:
    """Parse pasted `Items For Sale` content into normalized item counters."""

    remaining = collapse_whitespace(raw_value)
    items: Counter[str] = Counter()
    labels: dict[str, str] = {}

    while remaining:
        match = re.search(r"\s+x\s+(\d+)", remaining)
        if not match:
            break

        name = collapse_whitespace(remaining[: match.start()])
        quantity = int(match.group(1))
        if not name:
            break

        key = normalize_text(name)
        items[key] += quantity
        labels.setdefault(key, name)
        remaining = remaining[match.end() :].lstrip(" ,;")

    return items, labels


def summarize_counter(
    counter: Counter[str], labels: dict[str, str] | None = None
) -> list[str]:
    labels = labels or {}
    summary: list[str] = []
    for key in sorted(counter.keys()):
        display = labels.get(key) or key
        summary.append(f"{display} x {counter[key]}")
    return summary


def build_expected_items(
    items: Iterable[object],
) -> tuple[Counter[str], dict[str, str]]:
    counter: Counter[str] = Counter()
    labels: dict[str, str] = {}

    for item in items:
        name = collapse_whitespace(getattr(item, "type_name", ""))
        if not name:
            continue
        key = normalize_text(name)
        counter[key] += int(getattr(item, "quantity", 0) or 0)
        labels.setdefault(key, name)

    return counter, labels
