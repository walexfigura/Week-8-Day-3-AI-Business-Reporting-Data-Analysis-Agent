"""Turn messy source values into consistent analytical values.

Shopify, Meta and a hand-maintained Google Sheet all describe money, dates and
products differently:

    source         money          dates                     product id
    Shopify        "32.00"        "2026-11-02T01:15:00Z"    "NS-BTL-20"
    Meta Ads       "1234.56"      "2026-11-02" (acct tz)    (none — campaigns)
    Google Sheet   "$1,234.50"    "11/2/2026"               "ns-brs-cln "

The functions here are small and boring on purpose: each one does one
conversion, raises ``ValueError`` with a clear message when it cannot, and is
unit-tested. Row-level code catches those errors and records a data-quality
issue instead of silently inventing a value.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

_MONEY_CLEAN = re.compile(r"[,\s$]")
_HEADER_CLEAN = re.compile(r"[^a-z0-9]+")
_SKU_CLEAN = re.compile(r"\s+")
ALLOWED_CURRENCIES = {"USD"}


def normalize_header(header: str) -> str:
    """'Unit Cost (USD)' -> 'unit_cost_usd'."""
    return _HEADER_CLEAN.sub("_", header.strip().lower()).strip("_")


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_money(value: object, *, allow_negative: bool = False) -> Decimal:
    """Parse '1,234.50', '$1,234.50', '(12.00)' or 12.5 into Decimal('1234.50').

    Blank values raise ValueError: the caller decides whether missing is
    acceptable. Missing money is never silently treated as zero.
    """
    if is_blank(value):
        raise ValueError("missing amount")
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = _MONEY_CLEAN.sub("", text.strip("()"))
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not a valid amount: {value!r}") from exc
    if negative:
        amount = -amount
    if not amount.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    if amount < 0 and not allow_negative:
        raise ValueError(f"unexpected negative amount: {value!r}")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_int(value: object, *, allow_negative: bool = False) -> int:
    if is_blank(value):
        raise ValueError("missing integer")
    text = str(value).replace(",", "").strip()
    try:
        number = int(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a valid integer: {value!r}") from exc
    if Decimal(text) != number:
        raise ValueError(f"not a whole number: {value!r}")
    if number < 0 and not allow_negative:
        raise ValueError(f"unexpected negative number: {value!r}")
    return number


def parse_bool(value: object) -> bool | None:
    if is_blank(value):
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "y"}:
        return True
    if text in {"false", "no", "0", "n"}:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def parse_date(value: object) -> date:
    """Accept ISO dates ('2026-11-02') and US sheet dates ('11/2/2026')."""
    if is_blank(value):
        raise ValueError("missing date")
    text = str(value).strip()
    try:
        if "/" in text:
            month, day, year = (int(part) for part in text.split("/"))
            return date(year, month, day)
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"not a valid date: {value!r}") from exc


def parse_timestamp_utc(value: object) -> datetime:
    """Parse an ISO-8601 timestamp and return a timezone-aware UTC datetime.

    A timestamp without an offset is rejected rather than guessed: guessing the
    timezone silently moves late-evening orders into the wrong day.
    """
    if is_blank(value):
        raise ValueError("missing timestamp")
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def to_reporting_date(ts_utc: datetime, tz_name: str) -> date:
    """The calendar date of a UTC timestamp in the reporting timezone.

    Example: 2026-11-09T01:30:00Z is still Sunday 2026-11-08 in New York, so
    that order belongs to the week of Nov 2-8, not the week of Nov 9.
    """
    return ts_utc.astimezone(ZoneInfo(tz_name)).date()


def normalize_sku(value: object) -> str:
    """' ns-brs-cln ' -> 'NS-BRS-CLN'. Raises on blank SKUs."""
    if is_blank(value):
        raise ValueError("missing SKU")
    return _SKU_CLEAN.sub("", str(value)).upper()


def normalize_currency(value: object) -> str:
    """'usd ' -> 'USD'. Raises for anything outside ALLOWED_CURRENCIES.

    The project has no FX conversion, so a non-USD record cannot be summed with
    USD records. Rejecting it is safer than adding euros to dollars.
    """
    if is_blank(value):
        raise ValueError("missing currency")
    code = str(value).strip().upper()
    if code not in ALLOWED_CURRENCIES:
        raise ValueError(f"unsupported currency {code!r} (reporting currency is USD; no FX conversion modeled)")
    return code


def clean_text(value: object, max_len: int = 500) -> str:
    """Trim free text and cap its length. Free text is stored, never executed."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:max_len]
