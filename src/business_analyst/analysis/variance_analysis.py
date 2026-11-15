"""Explain a change in contribution profit with arithmetic that adds up.

Two complementary views, both exact:

1. **Component bridge** (a waterfall): previous profit + change in net sales
   - change in COGS - change in fulfillment - change in payment fees - change
   in ad spend = current profit. The steps must sum to the total change; the
   function checks this and reports ``reconciles: true/false``.

2. **Cost-rate view**: "what if every cost had stayed the same share of net
   sales?" Profit at the previous margin minus actual profit equals the sum
   of (rate change x current net sales) for each cost. This separates costs
   that simply grew WITH sales from costs that grew FASTER than sales.

These are accounting decompositions. They show WHICH components moved and by
how much — not WHY they moved. Causes need separate evidence.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import duckdb

from ..config import Period
from .metrics import ZERO, campaign_summary, contribution_profit, money, pct_change, product_summary, ratio

COMPONENTS = [
    # key, label, sign in profit
    ("net", "Net product sales", 1),
    ("cogs", "COGS", -1),
    ("fulfillment", "Fulfillment costs", -1),
    ("payment", "Payment processing fees", -1),
    ("ads", "Meta advertising spend", -1),
]


def profit_bridge(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    p, c = previous["_raw"], current["_raw"]
    if p["profit"] is None or c["profit"] is None:
        return {"available": False,
                "reason": "Contribution profit is incomplete for at least one period; a bridge would use guessed values.",
                "warnings": previous["warnings"] + current["warnings"]}
    steps = []
    total_impact = ZERO
    for key, label, sign in COMPONENTS:
        change = c[key] - p[key]
        impact = sign * change
        total_impact += impact
        steps.append({
            "component": key, "label": label, "previous": money(p[key]), "current": money(c[key]),
            "change": money(change), "change_pct": pct_change(c[key], p[key]), "profit_impact": money(impact),
        })
    profit_change = c["profit"] - p["profit"]
    decline_drivers = sorted((s for s in steps if s["profit_impact"] < 0), key=lambda s: s["profit_impact"])
    total_negative = sum((Decimal(str(s["profit_impact"])) for s in decline_drivers), ZERO)
    for s in decline_drivers:
        s["share_of_cost_increases_pct"] = ratio(Decimal(str(s["profit_impact"])), total_negative)
    return {
        "available": True,
        "previous_profit": money(p["profit"]),
        "current_profit": money(c["profit"]),
        "profit_change": money(profit_change),
        "profit_change_pct": pct_change(c["profit"], p["profit"]),
        "steps": steps,
        "sum_of_impacts": money(total_impact),
        "reconciles": total_impact == profit_change,
        "largest_negative_drivers": [s["component"] for s in decline_drivers],
        "interpretation_rule": ("Impacts are accounting contributions to the change, not causes. "
                                "Example: 'ad spend rose $X' is a fact; 'ads became less efficient because...' "
                                "is a hypothesis that needs more evidence."),
    }


def cost_rate_analysis(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    p, c = previous["_raw"], current["_raw"]
    if p["profit"] is None or c["profit"] is None or p["net"] == 0:
        return {"available": False, "reason": "Needs complete contribution profit and non-zero previous net sales."}
    rows = []
    total = ZERO
    for key, label, _sign in COMPONENTS[1:]:
        prev_rate = p[key] / p["net"]
        cur_rate = c[key] / c["net"] if c["net"] else ZERO
        proportional = p[key] * (c["net"] / p["net"])
        excess = c[key] - proportional  # == (cur_rate - prev_rate) * current net
        total += excess
        rows.append({
            "component": key, "label": label,
            "previous_pct_of_net_sales": round(float(prev_rate * 100), 2),
            "current_pct_of_net_sales": round(float(cur_rate * 100), 2),
            "proportional_cost_if_rate_unchanged": money(proportional),
            "actual_cost": money(c[key]),
            "cost_above_proportional": money(excess),
        })
    profit_at_previous_margin = c["net"] * (p["profit"] / p["net"])
    return {
        "available": True,
        "net_sales_growth_pct": pct_change(c["net"], p["net"]),
        "previous_contribution_margin_pct": ratio(p["profit"], p["net"]),
        "current_contribution_margin_pct": ratio(c["profit"], c["net"]),
        "profit_if_previous_margin_held": money(profit_at_previous_margin),
        "actual_profit": money(c["profit"]),
        "margin_gap": money(profit_at_previous_margin - c["profit"]),
        "components": rows,
        "sum_cost_above_proportional": money(total),
        "reconciles": abs((profit_at_previous_margin - c["profit"]) - total) < Decimal("0.01"),
        "note": "A cost 'above proportional' grew faster than net sales; a value near 0 means it scaled with sales.",
    }


def net_sales_bridge(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    p, c = previous["_raw"], current["_raw"]
    return [
        {"component": "gross_sales", "previous": money(p["gross"]), "current": money(c["gross"]),
         "change": money(c["gross"] - p["gross"]), "net_sales_impact": money(c["gross"] - p["gross"])},
        {"component": "discounts", "previous": money(p["discounts"]), "current": money(c["discounts"]),
         "change": money(c["discounts"] - p["discounts"]), "net_sales_impact": money(p["discounts"] - c["discounts"])},
        {"component": "refunds", "previous": money(p["refunds"]), "current": money(c["refunds"]),
         "change": money(c["refunds"] - p["refunds"]), "net_sales_impact": money(p["refunds"] - c["refunds"])},
    ]


def fee_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    pf, cf = previous["fees"], current["fees"]
    po, co = previous["sales"]["orders"], current["sales"]["orders"]
    for key in ("pick_pack", "packaging", "shipping_labels", "payment_processing"):
        pv, cv = pf.get(key), cf.get(key)
        out.append({
            "category": key, "previous": pv, "current": cv,
            "change": money(Decimal(str(cv)) - Decimal(str(pv))) if None not in (pv, cv) else None,
            "change_pct": pct_change(cv, pv),
            "previous_per_order": money(Decimal(str(pv)) / po) if pv is not None and po else None,
            "current_per_order": money(Decimal(str(cv)) / co) if cv is not None and co else None,
        })
    return out


def campaign_changes(con: duckdb.DuckDBPyConnection, previous: Period, current: Period) -> dict[str, Any]:
    prev = {c["campaign_id"]: c for c in campaign_summary(con, previous)}
    cur = {c["campaign_id"]: c for c in campaign_summary(con, current)}
    total_change = sum(((cur.get(k, {}).get("_spend") or ZERO) - (prev.get(k, {}).get("_spend") or ZERO))
                       for k in set(prev) | set(cur))
    rows = []
    for cid in sorted(set(prev) | set(cur)):
        a, b = prev.get(cid, {}), cur.get(cid, {})
        ps, cs = a.get("_spend") or ZERO, b.get("_spend") or ZERO
        rows.append({
            "campaign_id": cid,
            "campaign_name_untrusted_text": b.get("campaign_name") or a.get("campaign_name"),
            "status": b.get("status") or a.get("status"),
            "previous_spend": money(ps), "current_spend": money(cs), "spend_change": money(cs - ps),
            "spend_change_pct": pct_change(cs, ps),
            "share_of_total_spend_change_pct": ratio(cs - ps, total_change) if total_change else None,
            "previous_platform_roas": a.get("platform_roas"), "current_platform_roas": b.get("platform_roas"),
            "previous_cpm": a.get("cpm"), "current_cpm": b.get("cpm"),
            "previous_platform_purchases": a.get("platform_purchases"),
            "current_platform_purchases": b.get("platform_purchases"),
        })
    rows.sort(key=lambda r: r["spend_change"], reverse=True)
    return {"total_spend_change": money(total_change), "campaigns": rows,
            "note": "platform_roas uses Meta-attributed purchase value (not Shopify sales); compare trends, not totals."}


def product_changes(con: duckdb.DuckDBPyConnection, previous: Period, current: Period) -> list[dict[str, Any]]:
    prev = {p["sku"]: p for p in product_summary(con, previous)}
    cur = {p["sku"]: p for p in product_summary(con, current)}
    rows = []
    for sku in sorted(set(prev) | set(cur)):
        a, b = prev.get(sku, {}), cur.get(sku, {})
        rows.append({
            "sku": sku, "title": b.get("title") or a.get("title"),
            "previous_units": a.get("units", 0), "current_units": b.get("units", 0),
            "units_change_pct": pct_change(b.get("units", 0), a.get("units", 0)),
            "previous_net_sales": a.get("net_sales", 0.0), "current_net_sales": b.get("net_sales", 0.0),
            "net_sales_change_pct": pct_change(b.get("net_sales", 0.0), a.get("net_sales", 0.0)),
            "previous_margin_pct": a.get("product_margin_pct"), "current_margin_pct": b.get("product_margin_pct"),
            "margin_pct_point_change": (round(b["product_margin_pct"] - a["product_margin_pct"], 2)
                                        if a.get("product_margin_pct") is not None and b.get("product_margin_pct") is not None
                                        else None),
            "previous_share_pct": a.get("share_of_net_sales_pct"), "current_share_pct": b.get("share_of_net_sales_pct"),
            "cogs_complete": bool(a.get("cogs_complete", True) and b.get("cogs_complete", True)),
        })
    rows.sort(key=lambda r: (r["current_net_sales"] or 0) - (r["previous_net_sales"] or 0), reverse=True)
    return rows


def period_length_check(previous: Period, current: Period) -> list[str]:
    warnings = []
    if previous.days != current.days:
        warnings.append(f"Periods have different lengths ({previous.days} vs {current.days} days). Totals are not "
                        "comparable; compare daily averages or choose equal-length periods.")
    if previous.start <= current.end and current.start <= previous.end:
        warnings.append("Periods overlap; the same days are counted in both.")
    return warnings


def compare(con: duckdb.DuckDBPyConnection, previous: Period, current: Period) -> dict[str, Any]:
    """Full period-over-period comparison used by tools and reports."""
    prev, cur = contribution_profit(con, previous), contribution_profit(con, current)
    length_warnings = period_length_check(previous, current)
    headline = {}
    for key, getter in {
        "net_sales": lambda r: r["_raw"]["net"],
        "gross_sales": lambda r: r["_raw"]["gross"],
        "orders": lambda r: r["sales"]["orders"],
        "cogs": lambda r: r["_raw"]["cogs"],
        "fees_total": lambda r: r["_raw"]["fees"],
        "meta_ad_spend": lambda r: r["_raw"]["ads"],
        "contribution_profit": lambda r: r["_raw"]["profit"],
    }.items():
        a, b = getter(prev), getter(cur)
        headline[key] = {"previous": money(a) if not isinstance(a, int) else a,
                         "current": money(b) if not isinstance(b, int) else b,
                         "change": (money(b - a) if not isinstance(a, int) else b - a) if None not in (a, b) else None,
                         "change_pct": pct_change(b, a)}
    # Flag headline metrics whose source is missing days in either period.
    source_for = {"net_sales": "shopify_orders", "gross_sales": "shopify_orders", "orders": "shopify_orders",
                  "cogs": "shopify_orders", "fees_total": "fulfillment_and_payment_costs",
                  "meta_ad_spend": "meta_ads", "contribution_profit": None}
    for key, source in source_for.items():
        sources = [source] if source else ["shopify_orders", "fulfillment_and_payment_costs", "meta_ads"]
        complete = all(r["coverage"][src]["complete"] for r in (prev, cur) for src in sources)
        headline[key]["data_complete"] = complete
        if not complete:
            headline[key]["change_pct"] = None
            headline[key]["note"] = "Not comparable: source data is missing days in at least one period."
    headline["contribution_margin_pct"] = {"previous": prev["contribution_margin_pct"],
                                           "current": cur["contribution_margin_pct"]}
    daily_avg = None
    if length_warnings:
        daily_avg = {k: {"previous_per_day": money(Decimal(str(v["previous"])) / previous.days) if v["previous"] is not None else None,
                         "current_per_day": money(Decimal(str(v["current"])) / current.days) if v["current"] is not None else None}
                     for k, v in headline.items() if k in ("net_sales", "meta_ad_spend", "fees_total")}
    return {
        "previous_period": previous.as_dict(),
        "current_period": current.as_dict(),
        "comparable": not length_warnings and prev["complete"] and cur["complete"],
        "warnings": length_warnings + [f"previous: {w}" for w in prev["warnings"]] + [f"current: {w}" for w in cur["warnings"]],
        "headline": headline,
        "daily_averages": daily_avg,
        "profit_bridge": profit_bridge(prev, cur),
        "cost_rate_analysis": cost_rate_analysis(prev, cur),
        "net_sales_bridge": net_sales_bridge(prev, cur),
        "fee_changes": fee_changes(prev, cur),
        "_previous": prev, "_current": cur,
    }
