"""Schema, views, read-only access, and protection against double counting."""

from __future__ import annotations

import duckdb
import pytest

from business_analyst.database import (
    DatabaseNotInitializedError,
    connect_readonly,
    fetch_dicts,
    load_validation_queries,
)


def test_expected_tables_and_views_exist(con):
    names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    for t in ("orders", "order_items", "refunds", "products", "product_costs", "fulfillment_costs", "ad_campaigns",
              "ad_daily_performance", "business_targets", "data_quality_issues", "v_daily_contribution"):
        assert t in names


def test_readonly_connection_rejects_writes(con):
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM orders")
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE hack (x INT)")


def test_readonly_connection_blocks_file_access(con):
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv('pyproject.toml')").fetchall()
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")


def test_missing_database_gives_friendly_error(tmp_path):
    with pytest.raises(DatabaseNotInitializedError, match="init-db"):
        connect_readonly(tmp_path / "nope.duckdb")


def test_all_validation_queries_pass_on_base_data(con, settings):
    failing = [q.name for q in load_validation_queries(settings.sql_dir / "validation-queries.sql") if fetch_dicts(con, q.sql)]
    assert failing == []


def test_validation_query_file_parses(settings):
    queries = load_validation_queries(settings.sql_dir / "validation-queries.sql")
    assert len(queries) >= 15
    assert all(q.severity in {"info", "warning", "error"} and q.sql.upper().lstrip().startswith(("SELECT", "WITH")) for q in queries)


def test_daily_view_equals_independent_totals(con):
    view = con.execute("SELECT SUM(gross_sales), SUM(discounts), SUM(refunds), SUM(ad_spend), SUM(fees_total), SUM(orders) FROM v_daily_contribution").fetchone()
    direct = (
        con.execute("SELECT SUM(gross_amount) FROM order_items").fetchone()[0],
        con.execute("SELECT SUM(discount_amount) FROM order_items").fetchone()[0],
        con.execute("SELECT SUM(refund_amount) FROM refunds").fetchone()[0],
        con.execute("SELECT SUM(spend) FROM ad_daily_performance").fetchone()[0],
        con.execute("SELECT SUM(amount) FROM fulfillment_costs").fetchone()[0],
        con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
    )
    assert view == direct


def test_naive_joins_double_count_but_views_do_not(con):
    """The classic trap: order-level money repeated once per line item and per ad row."""
    naive_lines = con.execute("SELECT SUM(o.subtotal_price) FROM orders o JOIN order_items oi USING (order_id)").fetchone()[0]
    true = con.execute("SELECT SUM(subtotal_price) FROM orders").fetchone()[0]
    assert naive_lines > true
    naive_ads = con.execute("""SELECT SUM(o.subtotal_price) FROM orders o
                               JOIN ad_daily_performance a ON a.perf_date = o.order_date""").fetchone()[0]
    assert naive_ads > true * 3  # roughly once per campaign active that day
    via_view = con.execute("SELECT SUM(gross_sales - discounts) FROM v_daily_sales").fetchone()[0]
    assert via_view == true


def test_cost_join_never_duplicates_lines(con):
    lines = con.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
    joined = con.execute("SELECT COUNT(*) FROM v_line_costs").fetchone()[0]
    assert joined == lines  # effective-date ranges do not overlap, so each line matches at most one cost
