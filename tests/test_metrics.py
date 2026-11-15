"""KPI formulas, period comparisons and the variance bridge."""

from __future__ import annotations

from decimal import Decimal

from business_analyst.analysis.metrics import contribution_profit, pct_change, ratio, sales_summary
from business_analyst.analysis.variance_analysis import compare, period_length_check
from business_analyst.config import Period


def test_pct_change_edge_cases():
    assert pct_change(120, 100) == 20.0
    assert pct_change(18600, 30000) == -38.0
    assert pct_change(5, 0) is None  # undefined, not infinite
    assert pct_change(None, 10) is None
    assert pct_change(-50, -100) == 50.0  # uses |previous|


def test_ratio_handles_missing_and_zero():
    assert ratio(Decimal(1), Decimal(4)) == 25.0
    assert ratio(Decimal(1), Decimal(0)) is None
    assert ratio(None, Decimal(3)) is None


def test_sales_summary_definitions(con, settings):
    s = sales_summary(con, settings.current_week)
    assert s["net_sales"] == round(s["gross_sales"] - s["discounts"] - s["refunds"], 2) == 120000.0
    assert s["orders"] == 1620
    assert s["average_order_value"] == round((s["gross_sales"] - s["discounts"]) / s["orders"], 2)


def test_contribution_profit_components(con, settings):
    cp = contribution_profit(con, settings.current_week)
    assert cp["complete"] and cp["warnings"] == []
    assert cp["contribution_profit"] == 18600.0
    assert cp["contribution_margin_pct"] == 15.5
    assert cp["fees"]["fees_total"] == 17400.0
    assert round(cp["fees"]["fulfillment_total"] + cp["fees"]["payment_processing"], 2) == 17400.0
    assert cp["cost_ratios_pct_of_net_sales"]["meta_ad_spend"] == 30.0


def test_compare_headline_and_bridge(con, settings):
    cmp = compare(con, settings.previous_week, settings.current_week)
    h = cmp["headline"]
    assert (h["net_sales"]["change_pct"], h["meta_ad_spend"]["change_pct"], h["contribution_profit"]["change_pct"]) == (20.0, 80.0, -38.0)
    assert h["cogs"]["change_pct"] == 20.0 and h["fees_total"]["change_pct"] == 74.0
    b = cmp["profit_bridge"]
    assert b["reconciles"] and b["sum_of_impacts"] == b["profit_change"] == -11400.0
    impacts = {s["component"]: s["profit_impact"] for s in b["steps"]}
    assert impacts["net"] == 20000.0 and impacts["ads"] == -16000.0 and impacts["cogs"] == -8000.0
    assert round(impacts["fulfillment"] + impacts["payment"], 2) == -7400.0


def test_cost_rate_analysis_separates_proportional_growth(con, settings):
    cra = compare(con, settings.previous_week, settings.current_week)["cost_rate_analysis"]
    rows = {r["component"]: r for r in cra["components"]}
    assert cra["profit_if_previous_margin_held"] == 36000.0
    assert cra["margin_gap"] == cra["sum_cost_above_proportional"] == 17400.0
    assert rows["cogs"]["cost_above_proportional"] == 0.0
    assert rows["ads"]["cost_above_proportional"] == 12000.0


def test_period_length_and_overlap_warnings():
    a, b = Period.parse("2026-10-23", "2026-11-01"), Period.parse("2026-11-02", "2026-11-08")
    assert any("different lengths" in w for w in period_length_check(a, b))
    assert any("overlap" in w for w in period_length_check(Period.parse("2026-11-01", "2026-11-07"), b))
    assert period_length_check(Period.parse("2026-10-26", "2026-11-01"), b) == []


def test_period_outside_data_is_incomplete_not_zero(con):
    cp = contribution_profit(con, Period.parse("2026-11-09", "2026-11-15"))
    assert cp["complete"] is False
    assert cp["contribution_profit"] is None
