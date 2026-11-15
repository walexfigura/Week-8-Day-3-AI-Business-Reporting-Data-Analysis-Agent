"""Shared ingestion helpers: raw tables, CSV reading, column checks, de-duplication.

Every adapter (mock CSV or live API) returns ``RawTable`` objects whose rows
are plain ``dict[str, str]`` with normalized header names. That shared shape
is what lets the same normalization code serve both mock and live sources.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import DataQualityIssue
from .normalization import normalize_header


class IngestionError(RuntimeError):
    """A problem that makes it unsafe to build the database (e.g. a required column is missing)."""


@dataclass
class RawTable:
    name: str  # logical table, e.g. "orders"
    source: str  # where it came from, e.g. "shopify/orders.csv" or "shopify:graphql"
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


def read_csv_table(path: Path, name: str, source: str) -> RawTable:
    if not path.exists():
        raise IngestionError(f"Source file not found: {path}. Did you clone the full repository?")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise IngestionError(f"{source} is empty (no header row)")
        columns = [normalize_header(h) for h in reader.fieldnames]
        rows = []
        for raw in reader:
            values = list(raw.values())
            rows.append(dict(zip(columns, values, strict=False)))
    return RawTable(name=name, source=source, rows=rows, columns=columns)


def rows_from_values(values: list[list[Any]], name: str, source: str) -> RawTable:
    """Google Sheets API returns a list of rows; the first row is the header."""
    if not values:
        raise IngestionError(f"{source} returned no rows")
    columns = [normalize_header(str(h)) for h in values[0]]
    rows = []
    for raw in values[1:]:
        padded = list(raw) + [""] * (len(columns) - len(raw))
        rows.append(dict(zip(columns, [str(v) for v in padded], strict=False)))
    return RawTable(name=name, source=source, rows=rows, columns=columns)


def require_columns(table: RawTable, required: Iterable[str]) -> None:
    missing = [c for c in required if c not in table.columns]
    if missing:
        raise IngestionError(
            f"{table.source} is missing required column(s): {', '.join(missing)}. "
            f"Found: {', '.join(table.columns) or '(none)'}"
        )


def normalize_rows(
    table: RawTable,
    transform: Callable[[dict[str, Any]], dict[str, Any]],
    issues: list[DataQualityIssue],
    ref_field: str,
) -> list[dict[str, Any]]:
    """Apply ``transform`` to each row. Rows that fail are recorded and skipped — never guessed."""
    out = []
    for index, row in enumerate(table.rows, start=2):  # row 1 is the header
        try:
            out.append(transform(row))
        except (ValueError, KeyError) as exc:
            issues.append(DataQualityIssue(
                source=table.source, check="malformed_row", severity="error",
                message=f"Row {index} skipped: {exc}", record_ref=str(row.get(ref_field, f"row {index}")),
            ))
    return out


def dedupe(
    rows: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
    source: str,
    issues: list[DataQualityIssue],
) -> list[dict[str, Any]]:
    """Drop rows whose key was already seen.

    * Exact duplicates (same key, same values) usually come from overlapping
      exports or pagination. They are dropped and reported as a warning.
    * Conflicting duplicates (same key, different values) mean the sources
      disagree. The first row is kept and an error is reported so a human looks.
    """
    seen: dict[Any, dict[str, Any]] = {}
    exact: list[str] = []
    conflicts: list[str] = []
    for row in rows:
        k = key(row)
        if k in seen:
            (exact if seen[k] == row else conflicts).append(str(k))
            continue
        seen[k] = row
    if exact:
        issues.append(DataQualityIssue(
            source=source, check="duplicate_records", severity="warning",
            message=f"{len(exact)} exact duplicate row(s) removed (same key and values).",
            record_ref=", ".join(exact[:10]) + (" ..." if len(exact) > 10 else ""), count=len(exact),
        ))
    if conflicts:
        issues.append(DataQualityIssue(
            source=source, check="conflicting_duplicates", severity="error",
            message=f"{len(conflicts)} row(s) share a key with different values; kept the first occurrence.",
            record_ref=", ".join(conflicts[:10]), count=len(conflicts),
        ))
    return list(seen.values())


# Phrases that look like instructions aimed at an AI system. Source data is
# DATA: we flag these so humans can see them, and the agent is told to ignore them.
_INSTRUCTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"system override",
    r"note to (the )?ai",
    r"you are now",
    r"\bdrop\s+table\b",
    r"\bdelete\s+from\b",
    r"disregard (the )?(rules|instructions)",
]
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.IGNORECASE)


def looks_like_instructions(text: str) -> bool:
    return bool(text and _INSTRUCTION_RE.search(text))


def scan_free_text(
    rows: list[dict[str, Any]], fields: list[str], source: str, ref_field: str, issues: list[DataQualityIssue]
) -> None:
    hits = [str(r.get(ref_field)) for r in rows for f in fields if looks_like_instructions(str(r.get(f) or ""))]
    if hits:
        issues.append(DataQualityIssue(
            source=source, check="embedded_instructions", severity="warning",
            message=(f"{len(hits)} free-text field(s) contain instruction-like text (possible prompt injection). "
                     "Treated as untrusted data; never followed."),
            record_ref=", ".join(hits[:10]), count=len(hits),
        ))
