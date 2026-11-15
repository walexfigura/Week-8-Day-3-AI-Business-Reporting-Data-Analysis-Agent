"""DuckDB access: schema creation for the loader, read-only access for everyone else.

Two very different kinds of connection exist in this project:

* ``connect_writable`` – used ONLY by the ingestion loader while it builds a
  fresh database file. The language model never receives this connection.
* ``connect_readonly`` – used by every analytical tool. It opens the file in
  DuckDB's read-only mode with external file/network access disabled, so even
  a query that slipped past validation could not modify data or read files.

``run_readonly_sql`` adds the application-level guardrails for the optional
educational SQL tool: one SELECT statement, an allow-list of tables, a
deny-list of dangerous keywords and file-reading functions, a row limit and a
timeout. Defense in depth: the system prompt also forbids writes, but we do
not rely on the prompt.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


class DatabaseNotInitializedError(RuntimeError):
    pass


class UnsafeQueryError(ValueError):
    """The SQL was rejected before it reached the database."""


class QueryTimeoutError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- connections
def connect_writable(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def connect_readonly(path: Path) -> duckdb.DuckDBPyConnection:
    if not path.exists():
        raise DatabaseNotInitializedError(
            f"No database at {path}. Run `python run.py init-db` first (add --scenario NAME for a scenario)."
        )
    con = duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})
    con.execute("SET lock_configuration = true")  # queries cannot re-enable file access
    return con


def split_statements(sql_text: str) -> list[str]:
    return [s.query for s in duckdb.extract_statements(sql_text)]


def apply_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    for statement in split_statements(path.read_text(encoding="utf-8")):
        con.execute(statement)


# ----------------------------------------------------------------------------- validation queries
@dataclass
class ValidationQuery:
    name: str
    severity: str
    description: str
    sql: str


_HEADER = re.compile(r"--\s*name:\s*(?P<name>\w+)\s*\|\s*severity:\s*(?P<sev>\w+)\s*\|\s*(?P<desc>.+)")


def load_validation_queries(path: Path) -> list[ValidationQuery]:
    """Parse sql/validation-queries.sql into named checks."""
    queries: list[ValidationQuery] = []
    current: dict[str, str] | None = None
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _HEADER.match(line.strip())
        if match:
            if current:
                queries.append(ValidationQuery(current["name"], current["sev"], current["desc"], "\n".join(body).strip()))
            current, body = match.groupdict(), []
        elif current and not line.strip().startswith("--"):
            body.append(line)
    if current:
        queries.append(ValidationQuery(current["name"], current["sev"], current["desc"], "\n".join(body).strip()))
    return queries


# ----------------------------------------------------------------------------- helpers
def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def fetch_dicts(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def table_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' ORDER BY table_name"
    ).fetchall()]
    return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


# ----------------------------------------------------------------------------- safe SQL
ALLOWED_RELATIONS = {
    "products", "orders", "order_items", "refunds", "product_costs", "fulfillment_costs", "ad_campaigns",
    "ad_daily_performance", "business_targets", "data_quality_issues", "ingestion_runs",
    "v_order_lines", "v_line_costs", "v_daily_sales", "v_daily_refunds", "v_daily_cogs", "v_daily_fees",
    "v_daily_ads", "v_daily_contribution", "v_product_daily", "v_product_refunds", "v_campaign_daily",
}
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|export|import|install|load|pragma|set|reset|"
    r"call|checkpoint|vacuum|truncate|grant|revoke|merge|use|begin|commit|rollback)\b",
    re.IGNORECASE,
)
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(read_\w+|glob|parquet_\w+|sniff_csv|getenv|duckdb_\w+|query_table|query|iceberg_\w+|delta_scan|"
    r"sqlite_\w+|postgres_\w+|mysql_\w+|httpfs\w*|current_setting)\s*\(",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def validate_readonly_sql(sql: str, max_length: int = 5000) -> str:
    """Return the cleaned query or raise UnsafeQueryError explaining why it was rejected."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")
    if len(sql) > max_length:
        raise UnsafeQueryError(f"Query longer than {max_length} characters.")
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise UnsafeQueryError(f"Could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError("Exactly one statement is allowed (no ';'-separated batches).")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise UnsafeQueryError(f"Only SELECT queries are allowed; got {statements[0].type.name}.")
    cleaned = statements[0].query.strip().rstrip(";")
    scrubbed = _STRING_LITERAL.sub("''", _COMMENTS.sub(" ", cleaned))
    keyword = _FORBIDDEN_KEYWORDS.search(scrubbed)
    if keyword:
        raise UnsafeQueryError(f"Keyword '{keyword.group(1).upper()}' is not allowed in the read-only SQL tool.")
    function = _FORBIDDEN_FUNCTIONS.search(scrubbed)
    if function:
        raise UnsafeQueryError(f"Function '{function.group(1)}' is not allowed (no file, network or system access).")
    return cleaned


def run_readonly_sql(
    con: duckdb.DuckDBPyConnection, sql: str, *, row_limit: int = 200, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    """Validate and execute one read-only query with a row limit and timeout."""
    cleaned = validate_readonly_sql(sql)
    try:
        referenced = {t.lower() for t in con.get_table_names(cleaned)}
    except duckdb.Error:
        # get_table_names binds the query and fails on some valid SQL (e.g. JOIN ... USING).
        # Fall back to the relation names after FROM/JOIN, ignoring CTE names.
        scrubbed = _STRING_LITERAL.sub("''", _COMMENTS.sub(" ", cleaned))
        ctes = {m.lower() for m in re.findall(r"(\w+)\s+AS\s*\(", scrubbed, re.IGNORECASE)}
        referenced = {m.lower() for m in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)\b(?!\s*\()", scrubbed,
                                                    re.IGNORECASE)} - ctes
    unknown = sorted(referenced - ALLOWED_RELATIONS)
    if unknown:
        raise UnsafeQueryError(f"Unknown or disallowed table(s): {', '.join(unknown)}. Use get_available_tables.")

    cursor = con.cursor()
    timed_out = threading.Event()

    def _interrupt() -> None:
        timed_out.set()
        cursor.interrupt()

    timer = threading.Timer(timeout_seconds, _interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        result = cursor.execute(cleaned)
        columns = [d[0] for d in result.description]
        rows = result.fetchmany(row_limit + 1)
    except duckdb.Error as exc:
        if timed_out.is_set():
            raise QueryTimeoutError(f"Query exceeded the {timeout_seconds:g}s timeout and was cancelled.") from exc
        raise
    finally:
        timer.cancel()
        cursor.close()
    truncated = len(rows) > row_limit
    return {
        "columns": columns,
        "rows": [[to_jsonable(v) for v in row] for row in rows[:row_limit]],
        "row_count": min(len(rows), row_limit),
        "truncated": truncated,
        "row_limit": row_limit,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
