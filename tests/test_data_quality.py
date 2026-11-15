"""Duplicate detection, coverage gaps, anomaly rules and injection flags."""

from __future__ import annotations

from business_analyst.analysis.anomalies import detect_anomalies
from business_analyst.analysis.metrics import source_coverage
from business_analyst.database import connect_readonly
from business_analyst.ingestion.loader import build_database
from business_analyst.tools.registry import ToolContext, build_registry


def test_duplicate_records_are_removed_and_reported(settings, tmp_path):
    report = build_database(settings, "duplicate_records", db_path=tmp_path / "d.duckdb")
    dups = {i.source: i.count for i in report.issues if i.check == "duplicate_records"}
    assert dups["shopify/orders.csv"] == 30 and dups["shopify/refunds.csv"] == 4
    assert dups["meta/campaign-performance.csv"] == 4
    assert report.row_counts["orders"] == 2920  # same as the clean base data


def test_coverage_detects_missing_meta_days(scenario_db, settings):
    con = connect_readonly(scenario_db("misaligned_periods"))
    cov = source_coverage(con, settings.current_week)
    con.close()
    assert cov["meta_ads"]["missing_dates"] == ["2026-11-07", "2026-11-08"]
    assert cov["shopify_orders"]["complete"]


def test_base_anomalies(con, settings):
    rules = {a["rule"] for a in detect_anomalies(con, settings.previous_week, settings.current_week)["anomalies"]}
    assert {"ad_spend_increase", "ad_spend_outpaced_sales", "contribution_margin_drop", "campaign_spend_increase",
            "fees_per_order_increase"} <= rules
    assert "missing_product_cost" not in rules and "data_completeness" not in rules


def test_scenario_anomalies(scenario_db, settings):
    def rules(name):
        con = connect_readonly(scenario_db(name))
        found = {a["rule"] for a in detect_anomalies(con, settings.previous_week, settings.current_week)["anomalies"]}
        con.close()
        return found

    assert "refund_rate_increase" in rules("refund_spike")
    assert "cogs_rate_increase" in rules("product_mix_shift")
    assert "missing_product_cost" in rules("missing_cogs")
    assert "platform_value_exceeds_shopify" in rules("attribution_gap")
    mis = rules("misaligned_periods")
    assert "data_completeness" in mis and "ad_spend_increase" not in mis  # no conclusions from partial spend


def test_refund_spike_concentrated_in_tumbler(scenario_db, settings):
    con = connect_readonly(scenario_db("refund_spike"))
    out = build_registry().execute("get_refund_summary", {"start_date": "2026-11-02", "end_date": "2026-11-08"},
                                   ToolContext(con=con, settings=settings))
    con.close()
    assert out["data"]["by_sku"][0]["sku"] == "NS-TUM-30"
    assert out["data"]["refund_rate_pct_of_gross"] > 5


def test_ad_spend_no_lift_scenario(scenario_db, settings):
    from business_analyst.analysis.variance_analysis import compare

    con = connect_readonly(scenario_db("ad_spend_no_lift"))
    h = compare(con, settings.previous_week, settings.current_week)["headline"]
    con.close()
    assert h["meta_ad_spend"]["change_pct"] == 80.0 and h["net_sales"]["change_pct"] < 5


def test_data_quality_tool_reports_injection_and_coverage(con, settings):
    out = build_registry().execute("get_data_quality_report", {"start_date": "2026-10-26", "end_date": "2026-11-08"},
                                   ToolContext(con=con, settings=settings))
    data = out["data"]
    assert data["summary"]["failed_checks"] == []
    assert sum(1 for i in data["ingestion_issues"] if i["check_name"] == "embedded_instructions") == 2
    assert data["coverage"]["meta_ads"]["complete"]
    assert "security_notice" in out
