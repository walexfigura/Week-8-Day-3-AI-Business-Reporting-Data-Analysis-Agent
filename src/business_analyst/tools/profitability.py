"""Profitability tools: contribution profit, cost breakdown, product margins."""

from __future__ import annotations

from typing import Any

from ..analysis.metrics import contribution_profit, product_summary
from ..analysis.variance_analysis import product_changes
from .marketing import TWO_PERIODS
from .registry import DATE, ToolContext, ToolSpec, period_arg, two_periods


def get_contribution_profit(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return contribution_profit(ctx.require_con(), period_arg(args))


def get_cost_breakdown(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    cp = contribution_profit(ctx.require_con(), period)
    return {
        "period": period.as_dict(),
        "complete": cp["complete"],
        "warnings": cp["warnings"],
        "cogs": cp["cogs"],
        "fulfillment_and_payment": cp["fees"],
        "meta_ad_spend": cp["ads"]["ad_spend"],
        "cost_ratios_pct_of_net_sales": cp["cost_ratios_pct_of_net_sales"],
        "net_sales": cp["sales"]["net_sales"],
        "orders": cp["sales"]["orders"],
        "notes": ("Fulfillment = pick/pack + packaging + shipping labels (3PL and carrier invoices). Payment fees "
                  "come from the payment processor payout report. Costs are assigned to the order date."),
    }


def get_product_profitability(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    return {
        "period": period.as_dict(),
        "products": product_summary(ctx.require_con(), period),
        "definition": ("product_margin = product net sales (line sales - discounts - refunds) - COGS. Meta spend "
                       "and fulfillment fees are NOT allocated to products: the data has no valid per-product "
                       "attribution, so per-product contribution profit is not available."),
    }


def compare_products(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    previous, current = two_periods(args)
    return {"previous_period": previous.as_dict(), "current_period": current.as_dict(),
            "products": product_changes(ctx.require_con(), previous, current),
            "note": "Sorted by change in net sales. Margin excludes ads and fulfillment (not allocated to products)."}


SPECS = [
    ToolSpec(
        name="get_contribution_profit",
        description=("Contribution profit for a date range with every component (net sales, COGS, fulfillment, "
                     "payment fees, Meta spend), margin, cost ratios, data coverage and completeness warnings. "
                     "Returns null profit if any component is incomplete."),
        properties={"start_date": DATE, "end_date": DATE},
        handler=get_contribution_profit, category="profitability",
    ),
    ToolSpec(
        name="get_cost_breakdown",
        description=("COGS (with missing-cost details), fulfillment by category (pick/pack, packaging, shipping "
                     "labels), payment fees, per-order costs and cost ratios for a date range."),
        properties={"start_date": DATE, "end_date": DATE},
        handler=get_cost_breakdown, category="profitability",
    ),
    ToolSpec(
        name="get_product_profitability",
        description=("Per-SKU units, gross sales, discounts, refunds, net sales, COGS and product margin for a "
                     "date range, flagging SKUs with missing product costs."),
        properties={"start_date": DATE, "end_date": DATE},
        handler=get_product_profitability, category="profitability",
    ),
    ToolSpec(
        name="compare_products",
        description="Per-SKU changes in units, net sales, product margin and sales mix between two periods.",
        properties=TWO_PERIODS,
        handler=compare_products, category="profitability",
    ),
]
