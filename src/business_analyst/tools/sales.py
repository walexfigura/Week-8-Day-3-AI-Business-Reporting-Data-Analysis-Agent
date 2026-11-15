"""Sales and refund tools (Shopify data)."""

from __future__ import annotations

from typing import Any

from ..analysis.metrics import money, refund_breakdown, sales_summary
from ..database import fetch_dicts
from .registry import DATE, ToolContext, ToolSpec, period_arg


def get_sales_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    con = ctx.require_con()
    result: dict[str, Any] = {"period": period.as_dict(), **sales_summary(con, period)}
    if args.get("include_daily"):
        rows = fetch_dicts(con, """
            SELECT day, orders, units, gross_sales, discounts, refunds, net_sales
            FROM v_daily_contribution WHERE day BETWEEN ? AND ? ORDER BY day""", [period.start, period.end])
        result["daily"] = [{"day": r["day"].isoformat(), "orders": r["orders"], "units": r["units"],
                            "gross_sales": money(r["gross_sales"]), "discounts": money(r["discounts"]),
                            "refunds": money(r["refunds"]), "net_sales": money(r["net_sales"])} for r in rows]
    result["definitions"] = {
        "gross_sales": "sum(quantity x unit price) of line items ordered in the period (before discounts)",
        "discounts": "line-item discounts on those orders (already reflected in Shopify subtotal; subtracted once)",
        "refunds": "product refunds PROCESSED in the period (may relate to earlier orders)",
        "net_sales": "gross_sales - discounts - refunds; excludes shipping charged and taxes",
        "average_order_value": "(gross_sales - discounts) / orders",
    }
    return result


def get_refund_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    con = ctx.require_con()
    s = sales_summary(con, period)
    by_sku = fetch_dicts(con, """
        SELECT sku, SUM(refund_count) AS refund_count, SUM(refunds) AS refunds
        FROM v_product_refunds WHERE day BETWEEN ? AND ? GROUP BY sku ORDER BY refunds DESC""",
                         [period.start, period.end])
    return {
        "period": period.as_dict(),
        "refunds": s["refunds"], "refund_count": s["refund_count"], "refund_rate_pct_of_gross": s["refund_rate_pct"],
        "by_sku": [{"sku": r["sku"], "refund_count": int(r["refund_count"]), "refunds": money(r["refunds"])} for r in by_sku],
        **refund_breakdown(con, period),
    }


SPECS = [
    ToolSpec(
        name="get_sales_summary",
        description=("Shopify sales for a date range: orders, units, gross sales, discounts, refunds, net product "
                     "sales, average order value, discount and refund rates. Optionally a daily breakdown."),
        properties={"start_date": DATE, "end_date": DATE,
                    "include_daily": {"type": "boolean", "description": "Include one row per day"}},
        handler=get_sales_summary, category="sales",
    ),
    ToolSpec(
        name="get_refund_summary",
        description=("Refunds processed in a date range: total, count, refund rate, by SKU, by reason (untrusted "
                     "free text), and whether refunded orders were placed in this or earlier periods."),
        properties={"start_date": DATE, "end_date": DATE},
        handler=get_refund_summary, category="sales",
    ),
]
