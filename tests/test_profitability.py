"""Product profitability, missing COGS, and the profitability tools."""

from __future__ import annotations

from business_analyst.analysis.metrics import contribution_profit, product_summary
from business_analyst.database import connect_readonly
from business_analyst.tools.registry import ToolContext, build_registry

WEEK = {"start_date": "2026-11-02", "end_date": "2026-11-08"}


def test_product_net_sales_sum_to_total(con, settings):
    products = product_summary(con, settings.current_week)
    assert round(sum(p["net_sales"] for p in products), 2) == 120000.0
    assert round(sum(p["cogs"] for p in products), 2) == 48000.0
    assert round(sum(p["share_of_net_sales_pct"] for p in products), 0) == 100


def test_product_margins_are_plausible(con, settings):
    products = {p["sku"]: p for p in product_summary(con, settings.current_week)}
    assert products["NS-CUP-KID"]["product_margin_pct"] < products["NS-BTL-20"]["product_margin_pct"]
    assert all(p["cogs_complete"] for p in products.values())


def test_unit_cost_change_applied_by_effective_date(con):
    costs = dict(con.execute("""SELECT o.order_date >= DATE '2026-11-02', MAX(oi.quantity * 0 + pc.unit_cost)
        FROM v_line_costs pc JOIN orders o USING (order_id) JOIN order_items oi USING (line_item_id)
        WHERE pc.sku = 'NS-BTL-32' GROUP BY 1""").fetchall())
    assert float(costs[False]) == 12.75 and float(costs[True]) == 13.50


def test_missing_cogs_never_treated_as_zero(scenario_db, settings):
    con = connect_readonly(scenario_db("missing_cogs"))
    cp = contribution_profit(con, settings.current_week)
    products = {p["sku"]: p for p in product_summary(con, settings.current_week)}
    con.close()
    assert cp["contribution_profit"] is None and cp["contribution_margin_pct"] is None
    assert cp["cogs"]["cogs"] is None and cp["cogs"]["cogs_known_portion"] > 0
    assert cp["cogs"]["missing_cost_skus"][0]["sku"] == "NS-BTL-40"
    # The upper bound is larger than any real profit could be (missing costs are >= 0)
    assert cp["contribution_profit_upper_bound"] > 0
    assert products["NS-BTL-40"]["product_margin"] is None and products["NS-BTL-40"]["cogs_complete"] is False


def test_previous_week_unaffected_by_missing_cogs(scenario_db, settings):
    con = connect_readonly(scenario_db("missing_cogs"))
    cp = contribution_profit(con, settings.previous_week)
    con.close()
    assert cp["contribution_profit"] == 30000.0


def test_profitability_tools(con, settings):
    reg, ctx = build_registry(), ToolContext(con=con, settings=settings)
    cp = reg.execute("get_contribution_profit", WEEK, ctx)
    assert cp["ok"] and cp["evidence_id"] == "E1" and cp["data"]["contribution_profit"] == 18600.0
    assert "_raw" not in cp["data"]  # private fields never reach the model
    costs = reg.execute("get_cost_breakdown", WEEK, ctx)
    assert costs["evidence_id"] == "E2" and costs["data"]["fulfillment_and_payment"]["fees_total"] == 17400.0
    prods = reg.execute("get_product_profitability", WEEK, ctx)
    assert "not allocated" in prods["data"]["definition"].lower()
    cmp = reg.execute("compare_products", {"previous_start": "2026-10-26", "previous_end": "2026-11-01",
                                           "current_start": "2026-11-02", "current_end": "2026-11-08"}, ctx)
    assert {p["sku"] for p in cmp["data"]["products"]} >= {"NS-KIT-HIK", "NS-BTL-20"}


def test_campaign_tools_identify_the_main_spend_increase(con, settings):
    reg, ctx = build_registry(), ToolContext(con=con, settings=settings)
    out = reg.execute("compare_campaigns", {"previous_start": "2026-10-26", "previous_end": "2026-11-01",
                                            "current_start": "2026-11-02", "current_end": "2026-11-08"}, ctx)
    top = out["data"]["campaigns"][0]
    assert top["campaign_id"] == "120210000000103" and top["spend_change"] == 13500.0
    assert out["data"]["total_spend_change"] == 16000.0
    assert "attribution_caveat" in out["data"]
