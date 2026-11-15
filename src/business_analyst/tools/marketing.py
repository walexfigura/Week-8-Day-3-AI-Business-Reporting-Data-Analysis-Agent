"""Meta advertising tools. Spend is a cost; platform purchases/value are attributed, not additive revenue."""

from __future__ import annotations

from typing import Any

from ..analysis.metrics import ad_summary, campaign_summary, money, sales_summary
from ..analysis.variance_analysis import campaign_changes
from ..database import fetch_dicts
from .registry import DATE, ToolContext, ToolSpec, period_arg, two_periods

ATTRIBUTION_CAVEAT = ("Meta-reported purchases and purchase value are attributed by Meta's model and overlap with "
                      "Shopify orders. Never add them to Shopify sales, and do not treat them as proof of "
                      "incremental sales.")


def get_ad_spend_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    con = ctx.require_con()
    ads = ad_summary(con, period)
    shopify = sales_summary(con, period)
    return {
        "period": period.as_dict(),
        **{k: v for k, v in ads.items() if not k.startswith("_")},
        "shopify_net_sales_same_period": shopify["net_sales"],
        "shopify_orders_same_period": shopify["orders"],
        "blended_mer_shopify_net_sales_per_ad_dollar": (round(shopify["net_sales"] / ads["ad_spend"], 2)
                                                        if ads["ad_spend"] else None),
        "by_campaign": [{k: v for k, v in c.items() if not k.startswith("_")} for c in campaign_summary(con, period)],
        "attribution_caveat": ATTRIBUTION_CAVEAT,
    }


def get_campaign_performance(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    period = period_arg(args)
    con = ctx.require_con()
    campaign_id = args.get("campaign_id") or None
    summary = campaign_summary(con, period, campaign_id)
    meta = fetch_dicts(con, """
        SELECT campaign_id, objective, status, start_date, stop_date, daily_budget, product_focus, notes
        FROM ad_campaigns WHERE (? IS NULL OR campaign_id = ?) ORDER BY campaign_id""", [campaign_id, campaign_id])
    result: dict[str, Any] = {
        "period": period.as_dict(),
        "campaigns": [{k: v for k, v in c.items() if not k.startswith("_")} for c in summary],
        "campaign_metadata": [{
            "campaign_id": m["campaign_id"], "objective": m["objective"], "status": m["status"],
            "start_date": m["start_date"].isoformat() if m["start_date"] else None,
            "stop_date": m["stop_date"].isoformat() if m["stop_date"] else None,
            "daily_budget": money(m["daily_budget"]), "product_focus_untrusted_text": m["product_focus"],
            "notes_untrusted_text": m["notes"],
        } for m in meta],
        "attribution_caveat": ATTRIBUTION_CAVEAT,
    }
    if campaign_id:
        daily = fetch_dicts(con, """
            SELECT day, spend, impressions, clicks, platform_purchases, platform_purchase_value
            FROM v_campaign_daily WHERE campaign_id = ? AND day BETWEEN ? AND ? ORDER BY day""",
                            [campaign_id, period.start, period.end])
        result["daily"] = [{"day": d["day"].isoformat(), "spend": money(d["spend"]), "impressions": d["impressions"],
                            "clicks": d["clicks"], "platform_purchases": d["platform_purchases"],
                            "platform_purchase_value": money(d["platform_purchase_value"])} for d in daily]
        if not summary:
            result["note"] = f"No spend rows for campaign {campaign_id} in this period."
    return result


def compare_campaigns(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    previous, current = two_periods(args)
    data = campaign_changes(ctx.require_con(), previous, current)
    return {"previous_period": previous.as_dict(), "current_period": current.as_dict(), **data,
            "attribution_caveat": ATTRIBUTION_CAVEAT}


TWO_PERIODS = {"previous_start": DATE, "previous_end": DATE, "current_start": DATE, "current_end": DATE}

SPECS = [
    ToolSpec(
        name="get_ad_spend_summary",
        description=("Meta advertising totals for a date range (spend, impressions, clicks, CTR, CPC, CPM, "
                     "Meta-reported purchases/value/ROAS) with a per-campaign breakdown and Shopify net sales "
                     "for the same dates for context."),
        properties={"start_date": DATE, "end_date": DATE},
        handler=get_ad_spend_summary, category="marketing",
    ),
    ToolSpec(
        name="get_campaign_performance",
        description=("Campaign-level Meta performance and campaign metadata for a date range. Pass a campaign_id "
                     "for daily detail on one campaign, or null for all campaigns."),
        properties={"start_date": DATE, "end_date": DATE,
                    "campaign_id": {"type": ["string", "null"], "description": "Meta campaign ID, or null"}},
        handler=get_campaign_performance, category="marketing",
    ),
    ToolSpec(
        name="compare_campaigns",
        description=("Per-campaign spend change between two periods, each campaign's share of the total spend "
                     "change, and changes in CPM and Meta-reported ROAS."),
        properties=TWO_PERIODS,
        handler=compare_campaigns, category="marketing",
    ),
]
