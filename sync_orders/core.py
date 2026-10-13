"""Core logic: phone normalization, deduplication and stats calculation."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

RU_PREFIX = "7"
RU_LENGTH = 11


def normalize_phone(raw: Any) -> Optional[str]:
    """Bring any human phone format to +7XXXXXXXXXX.

    Rules:
      * only digits are taken into account, any separators/parentheses/letters
        and spaces are ignored;
      * a leading 8 is rewritten to 7 (8 999 ... -> 7 999 ...);
      * anything that does not reduce to exactly 11 digits, or whose country
        code is neither 7 nor 8, is rejected (None).

    Returns ``+7XXXXXXXXXX`` or None for an invalid number.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        raw = str(raw)
    if not isinstance(raw, str):
        return None

    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None

    if digits[0] == "8":
        digits = RU_PREFIX + digits[1:]

    if len(digits) != RU_LENGTH or digits[0] != RU_PREFIX:
        return None

    return "+" + digits


def coerce_amount(raw: Any) -> float:
    """Amount as a number; anything unusable counts as 0."""
    if isinstance(raw, bool):
        return 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        try:
            value = float(str(raw).strip().replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            return 0.0
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return value


def coerce_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    return str(raw).strip()


def parse_created_at(raw: Any) -> Tuple[datetime, str]:
    """Return (sortable datetime, normalized ISO string). Unparsable -> epoch."""
    text = coerce_text(raw)
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return datetime(1970, 1, 1, tzinfo=timezone.utc), text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), parsed.astimezone(timezone.utc).isoformat()


@dataclass
class Order:
    """A raw order from the source API, already parsed."""

    source_id: str
    name: str
    phone_raw: str
    phone: Optional[str]
    email: str
    amount: float
    amount_raw: Any
    created_at_text: str
    created_at: datetime

    @property
    def valid(self) -> bool:
        return self.phone is not None

    @property
    def sort_key(self) -> Tuple[float, datetime, str]:
        """Winner of a duplicate group: max amount, then latest created_at.

        The trailing source id keeps the ordering total (and therefore the
        result deterministic) when amount and created_at are equal.
        """
        return (self.amount, self.created_at, self.source_id)


@dataclass
class Stats:
    received: int = 0
    unique: int = 0
    duplicates_merged: int = 0
    invalid_dropped: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "received": self.received,
            "unique": self.unique,
            "duplicates_merged": self.duplicates_merged,
            "invalid_dropped": self.invalid_dropped,
        }


@dataclass
class SyncResult:
    clients: List[Dict[str, Any]] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)

    def as_dict(self) -> Dict[str, Any]:
        return {"clients": self.clients, "stats": self.stats.as_dict()}


def _as_source_id(raw: Any, position: int) -> str:
    text = coerce_text(raw)
    return text if text else str(position)


def parse_order(raw: Any, position: int) -> Order:
    if not isinstance(raw, dict):
        raise ValueError("order #%d is not a JSON object" % position)
    created_dt, created_iso = parse_created_at(raw.get("created_at"))
    phone_raw = raw.get("phone")
    amount_raw = raw.get("amount")
    return Order(
        source_id=_as_source_id(raw.get("id"), position),
        name=coerce_text(raw.get("name")),
        phone_raw=coerce_text(phone_raw),
        phone=normalize_phone(phone_raw),
        email=coerce_text(raw.get("email")),
        amount=coerce_amount(amount_raw),
        amount_raw=amount_raw,
        created_at_text=created_iso,
        created_at=created_dt,
    )


def _public_amount(value: float) -> Any:
    """Keep ints as ints, so the JSON payload matches the source data."""
    if float(value).is_integer():
        return int(value)
    return value


def group_winners(parsed: List[Order]) -> Dict[str, Order]:
    """phone -> the winning order (max amount, then latest created_at)."""
    winners: Dict[str, Order] = {}
    for order in parsed:
        if not order.valid:
            continue
        current = winners.get(order.phone)  # type: ignore[arg-type]
        if current is None or order.sort_key > current.sort_key:
            winners[order.phone] = order  # type: ignore[index]
    return winners


def build_result(orders: Iterable[Any]) -> SyncResult:
    """Pure function: raw API payload -> {clients, stats}."""
    parsed: List[Order] = [
        parse_order(raw, index) for index, raw in enumerate(orders)
    ]

    stats = Stats(received=len(parsed))
    winners = group_winners(parsed)
    valid_count = sum(1 for order in parsed if order.valid)

    clients = [
        {
            "name": winner.name,
            "phone": winner.phone,
            "email": winner.email,
            "amount": _public_amount(winner.amount),
        }
        for _, winner in sorted(winners.items())
    ]

    stats.unique = len(clients)
    stats.duplicates_merged = valid_count - len(clients)
    stats.invalid_dropped = len(parsed) - valid_count

    return SyncResult(clients=clients, stats=stats)