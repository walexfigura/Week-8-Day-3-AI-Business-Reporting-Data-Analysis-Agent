"""Transparent, rule-based anomaly detection.

These are simple thresholds — not machine learning, not statistics. Each rule
says what it checked, the observed value, and the threshold, so a person can
disagree with it. The agent decides which flagged items deserve discussion.

Thresholds live in ``DEFAULT_THRESHOLDS`` and are documented in
agent/workflows/anomaly-detection.md. Change them there and here together.
"""

from __future__ import annotations

from typing import Any

import duckdb

from ..config import Period
from .variance_analysis import campaign_changes, compare, product_changes

DEFAULT_THRESHOLDS: dict[str, float] = {
    "ad_spend_increase_pct": 30.0,           # total Meta spend up more than this %
    "ad_spend_vs_sales_growth_gap_pts": 20.0, # spend growth exceeds net-sales growth by this many points
    "contribution_margin_drop_pts": 5.0,      # margin down more than this many percentage points
    "refund_rate_increase_pts": 1.5,          # refund rate up more than this many points
    "campaign_spend_increase_pct": 50.0,      # a campaign's spend up more than this %...
    "campaign_spend_increase_min_usd": 1000.0,  # ...and by at least this many dollars
    "fees_per_order_increase_pct": 15.0,
    "cogs_pct_increase_pts": 2.0,
    "product_margin_drop_pts": 5.0,
    "platform_roas_drop_pct": 20.0,
}


def _flag(rule: str, severity: str, observed: Any, threshold: Any, message: str, **extra: Any) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "observed": observed, "threshold": threshold, "message": message, **extra}


def detect_anomalies(con: duckdb.DuckDBPyConnection, previous: Period, current: Period,
                     thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    cmp = compare(con, previous, current)
    prev, cur = cmp["_previous"], cmp["_current"]
    h = cmp["headline"]
    flags: list[dict[str, Any]] = []

    # Data completeness first: everything else is less trustworthy when data is missing.
    for w in cmp["warnings"]:
        if "COGS incomplete" in w:
            continue  # reported per SKU by the missing_product_cost rule below
        flags.append(_flag("data_completeness", "high", None, "complete, equal-length periods", w))

    # Spend-based rules are skipped when Meta data is missing days: a partial
    # total would make spend look lower than it was. (data_completeness covers it.)
    ads_complete = prev["coverage"]["meta_ads"]["complete"] and cur["coverage"]["meta_ads"]["complete"]
    spend_pct = h["meta_ad_spend"]["change_pct"]
    sales_pct = h["net_sales"]["change_pct"]
    if spend_pct is not None and spend_pct > t["ad_spend_increase_pct"]:
        flags.append(_flag("ad_spend_increase", "high", spend_pct, t["ad_spend_increase_pct"],
                           f"Meta ad spend changed {spend_pct:+.1f}% (threshold +{t['ad_spend_increase_pct']:.0f}%)."))
    if spend_pct is not None and sales_pct is not None and spend_pct - sales_pct > t["ad_spend_vs_sales_growth_gap_pts"]:
        flags.append(_flag("ad_spend_outpaced_sales", "high", round(spend_pct - sales_pct, 2),
                           t["ad_spend_vs_sales_growth_gap_pts"],
                           f"Ad spend growth ({spend_pct:+.1f}%) exceeded net sales growth ({sales_pct:+.1f}%) "
                           f"by {spend_pct - sales_pct:.1f} points."))

    pm, cm = prev["contribution_margin_pct"], cur["contribution_margin_pct"]
    if pm is not None and cm is not None and pm - cm > t["contribution_margin_drop_pts"]:
        flags.append(_flag("contribution_margin_drop", "high", round(cm - pm, 2), -t["contribution_margin_drop_pts"],
                           f"Contribution margin fell from {pm:.1f}% to {cm:.1f}% ({cm - pm:+.1f} points)."))

    pr, cr = prev["sales"]["refund_rate_pct"], cur["sales"]["refund_rate_pct"]
    if pr is not None and cr is not None and cr - pr > t["refund_rate_increase_pts"]:
        flags.append(_flag("refund_rate_increase", "medium", round(cr - pr, 2), t["refund_rate_increase_pts"],
                           f"Refund rate rose from {pr:.2f}% to {cr:.2f}% of gross sales."))

    pf, cf = prev["fees"]["fees_per_order"], cur["fees"]["fees_per_order"]
    if pf and cf and (cf - pf) / pf * 100 > t["fees_per_order_increase_pct"]:
        flags.append(_flag("fees_per_order_increase", "medium", round((cf - pf) / pf * 100, 2),
                           t["fees_per_order_increase_pct"],
                           f"Fulfillment + payment fees per order rose from ${pf:.2f} to ${cf:.2f}."))

    pc, cc = prev["cost_ratios_pct_of_net_sales"]["cogs"], cur["cost_ratios_pct_of_net_sales"]["cogs"]
    if pc is not None and cc is not None and cc - pc > t["cogs_pct_increase_pts"]:
        flags.append(_flag("cogs_rate_increase", "medium", round(cc - pc, 2), t["cogs_pct_increase_pts"],
                           f"COGS rose from {pc:.1f}% to {cc:.1f}% of net sales (possible mix or cost change)."))

    for label, result in (("previous", prev), ("current", cur)):
        for m in result["cogs"]["missing_cost_skus"]:
            flags.append(_flag("missing_product_cost", "high", m["units"], 0,
                               f"{label} period: SKU {m['sku']} sold {m['units']} unit(s) with no product cost; "
                               "COGS and contribution profit are incomplete.", sku=m["sku"]))

    pr_roas, cr_roas = prev["ads"]["platform_roas"], cur["ads"]["platform_roas"]
    if ads_complete and pr_roas and cr_roas and (pr_roas - cr_roas) / pr_roas * 100 > t["platform_roas_drop_pct"]:
        flags.append(_flag("platform_roas_drop", "medium", round((cr_roas - pr_roas) / pr_roas * 100, 2),
                           -t["platform_roas_drop_pct"],
                           f"Meta-reported ROAS fell from {pr_roas:.2f} to {cr_roas:.2f} (attribution-based metric)."))
    pv, net = cur["ads"]["platform_purchase_value"], cur["sales"]["net_sales"]
    if pv is not None and net and pv > net:
        flags.append(_flag("platform_value_exceeds_shopify", "high", pv, net,
                           f"Meta-attributed purchase value (${pv:,.2f}) exceeds total Shopify net sales "
                           f"(${net:,.2f}). Platform attribution cannot be reconciled with store revenue."))

    for c in (campaign_changes(con, previous, current)["campaigns"] if ads_complete else []):
        pct, change = c["spend_change_pct"], c["spend_change"]
        if change >= t["campaign_spend_increase_min_usd"] and (pct is None or pct > t["campaign_spend_increase_pct"]):
            flags.append(_flag("campaign_spend_increase", "high" if change > 5000 else "medium", change,
                               t["campaign_spend_increase_min_usd"],
                               f"Campaign {c['campaign_id']} spend rose ${change:,.2f}"
                               f"{f' ({pct:+.1f}%)' if pct is not None else ' (new spend)'}; "
                               f"{c['share_of_total_spend_change_pct']}% of the total change.",
                               campaign_id=c["campaign_id"]))

    for p in product_changes(con, previous, current):
        d = p["margin_pct_point_change"]
        if d is not None and d < -t["product_margin_drop_pts"]:
            flags.append(_flag("product_margin_drop", "medium", d, -t["product_margin_drop_pts"],
                               f"{p['sku']} product margin changed {d:+.1f} points.", sku=p["sku"]))

    order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: order.get(f["severity"], 3))
    return {
        "method": "Deterministic threshold rules (not machine learning). Thresholds are configurable.",
        "thresholds": t,
        "previous_period": previous.as_dict(),
        "current_period": current.as_dict(),
        "anomalies": flags,
        "count": len(flags),
    }
