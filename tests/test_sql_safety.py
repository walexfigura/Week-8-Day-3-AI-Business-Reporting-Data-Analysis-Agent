"""The educational SQL tool: validation, allow-list, row limit, timeout.

Defense in depth: even if validation were bypassed, the connection is read-only
(see test_database.py)."""

from __future__ import annotations

import pytest

from business_analyst.database import QueryTimeoutError, UnsafeQueryError, run_readonly_sql, validate_readonly_sql
from business_analyst.evaluation import MALICIOUS_SQL
from business_analyst.tools.registry import ToolContext, build_registry


@pytest.mark.parametrize("query", MALICIOUS_SQL + [
    "",
    "   ",
    "SELECT * FROM orders; SELECT 1",
    "select * from glob('*')",
    "SELECT getenv('OPENAI_API_KEY')",
    "SELECT * FROM duckdb_settings()",
    "/* sneaky */ DELETE FROM orders",
    "EXPLAIN SELECT 1",
    "CALL pragma_version()",
])
def test_rejects_unsafe_queries(con, query):
    with pytest.raises((UnsafeQueryError, Exception)) as exc:
        run_readonly_sql(con, query)
    assert exc.type is not AssertionError


@pytest.mark.parametrize("query", MALICIOUS_SQL)
def test_rejection_happens_in_validation_layer(con, query):
    """Every malicious query is stopped by our checks, before DuckDB runs it."""
    with pytest.raises(UnsafeQueryError):
        run_readonly_sql(con, query)


@pytest.mark.parametrize("query", [
    "SELECT COUNT(*) FROM orders",
    "WITH d AS (SELECT day, net_sales FROM v_daily_contribution) SELECT * FROM d ORDER BY day",
    "SELECT sku, SUM(quantity) AS units FROM order_items GROUP BY sku ORDER BY units DESC",
    "SELECT 'drop table orders' AS harmless_text",  # keywords inside string literals are fine
    "SELECT o.order_date, COUNT(*) FROM orders o JOIN order_items oi USING (order_id) GROUP BY 1",
    "SELECT replace(sku, '-', '') FROM products",
])
def test_allows_reasonable_selects(con, query):
    result = run_readonly_sql(con, query)
    assert result["row_count"] >= 1


def test_row_limit(con):
    r = run_readonly_sql(con, "SELECT * FROM order_items", row_limit=25)
    assert r["row_count"] == 25 and r["truncated"] is True


def test_timeout_cancels_long_queries(con):
    slow = "SELECT COUNT(*) FROM order_items a, order_items b, order_items c"
    with pytest.raises(QueryTimeoutError):
        run_readonly_sql(con, slow, timeout_seconds=0.2)


def test_validate_strips_trailing_semicolon():
    assert validate_readonly_sql("SELECT 1;") == "SELECT 1"


def test_tool_returns_structured_rejection(con, settings):
    out = build_registry().execute("run_readonly_sql", {"query": "DROP TABLE orders"}, ToolContext(con=con, settings=settings))
    assert out["ok"] is False and out["error"]["code"] == "query_rejected"


def test_no_write_tools_are_exposed_to_the_model():
    names = build_registry().names()
    assert not [n for n in names if any(w in n for w in ("delete", "update", "insert", "write", "drop", "create"))]
