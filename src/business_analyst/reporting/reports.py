"""Weekly business review / investigation reports: Markdown + JSON + PNG charts.

Everything numeric in a report comes from ONE call to the analysis layer
(``build_summary``). Tables, charts and the JSON summary are all rendered from
that single dictionary, so they cannot disagree. The AI narrative (when
available) is checked against the same numbers and any number it cannot be
matched to is listed under "Guardrail warnings".

Without an API key, a deterministic, facts-only summary is written instead of
the AI narrative. It never speculates about causes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..analysis.anomalies import detect_anomalies
from ..analysis.metrics import money, refund_breakdown
from ..analysis.variance_analysis import campaign_changes, compare, product_changes
from ..config import Period, Settings
from ..database import connect_readonly, fetch_dicts, load_validation_queries
from ..guardrails import collect_numbers, ungrounded_numbers
from ..ingestion.loader import ensure_database, load_scenario
from ..models import AnalysisResult
from . import charts


@dataclass
class ReportOutput:
    folder: Path
    markdown: Path
    summary_json: Path
    charts: list[Path]
    consistency_issues: list[str]


# ----------------------------------------------------------------------------- data
def _target_status(con, cmp: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = {t["metric"]: t for t in fetch_dicts(con, "SELECT * FROM business_targets")}
    cur = cmp["_current"]
    actual = {
        "contribution_margin": cur["contribution_margin_pct"],
        "net_sales_growth_wow": cmp["headline"]["net_sales"]["change_pct"],
        "ad_spend_pct_of_net_sales": cur["cost_ratios_pct_of_net_sales"]["meta_ad_spend"],
        "blended_mer": cur["blended_mer"],
        "meta_platform_roas": cur["ads"]["platform_roas"],
        "cogs_pct_of_net_sales": cur["cost_ratios_pct_of_net_sales"]["cogs"],
        "fees_per_order": cur["fees"]["fees_per_order"],
        "refund_rate": cur["sales"]["refund_rate_pct"],
    }
    ads_complete = cmp["headline"]["meta_ad_spend"].get("data_complete", True)
    if not ads_complete:  # partial ad data would make efficiency look better than it was
        for key in ("ad_spend_pct_of_net_sales", "blended_mer", "meta_platform_roas"):
            actual[key] = None
    rows = []
    for metric, t in targets.items():
        if metric == "product_gross_margin":
            below = [p["sku"] for p in products if p["current_margin_pct"] is not None and p["current_margin_pct"] < t["target_value"]]
            rows.append({"metric": metric, "target": t["target_value"], "direction": t["direction"], "unit": t["unit"],
                         "actual": f"{len(below)} SKU(s) below", "status": "missed" if below else "met", "detail": ", ".join(below)})
            continue
        value = actual.get(metric)
        if value is None:
            status = "unavailable"
        elif t["direction"] == "min":
            status = "met" if value >= t["target_value"] else "missed"
        else:
            status = "met" if value <= t["target_value"] else "missed"
        rows.append({"metric": metric, "target": t["target_value"], "direction": t["direction"], "unit": t["unit"],
                     "actual": value, "status": status, "detail": ""})
    return rows


def build_summary(settings: Settings, scenario: str | None, previous: Period, current: Period,
                  db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or ensure_database(settings, scenario)
    con = connect_readonly(path)
    try:
        cmp = compare(con, previous, current)
        prod = product_changes(con, previous, current)
        daily = fetch_dicts(con, """
            SELECT day, net_sales, ad_spend FROM v_daily_contribution WHERE day BETWEEN ? AND ? ORDER BY day""",
                            [previous.start, current.end])
        issues = fetch_dicts(con, "SELECT source, check_name, severity, message, issue_count FROM data_quality_issues ORDER BY issue_id")
        failed = [c.name for c in load_validation_queries(settings.sql_dir / "validation-queries.sql")
                  if fetch_dicts(con, c.sql)]
        summary = {
            "meta": {
                "company": "Northstar Supply (fictional)",
                "scenario": load_scenario(settings, scenario)["name"],
                "data_source": settings.data_source,
                "previous_period": previous.as_dict(),
                "current_period": current.as_dict(),
                "timezone": settings.reporting_timezone,
                "currency": "USD",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "database": path.name,
            },
            "comparison": {k: v for k, v in cmp.items() if not k.startswith("_")},
            "previous": {k: v for k, v in cmp["_previous"].items() if not k.startswith("_")},
            "current": {k: v for k, v in cmp["_current"].items() if not k.startswith("_")},
            "campaigns": campaign_changes(con, previous, current),
            "products": prod,
            "refunds_current": refund_breakdown(con, current),
            "anomalies": detect_anomalies(con, previous, current),
            "targets": _target_status(con, cmp, prod),
            "data_quality": {"ingestion_issues": issues, "failed_validation_checks": failed},
            "daily": [{"day": d["day"].isoformat(), "net_sales": money(d["net_sales"]),
                       "ad_spend": money(d["ad_spend"])} for d in daily],
            "_raw": {"previous": cmp["_previous"]["_raw"], "current": cmp["_current"]["_raw"]},
        }
    finally:
        con.close()
    return summary


# ----------------------------------------------------------------------------- formatting helpers
def usd(v: Any, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ("−" if v < 0 else "")
    return f"{sign}${abs(v):,.2f}"


def pct(v: Any, signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _cell(c: Any) -> str:
    """Escape a value for a Markdown table cell (pipes would split the cell; newlines end the row)."""
    if c is None:
        return ""
    return str(c).replace("\\|", "|").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def facts_only_summary(s: dict[str, Any]) -> list[str]:
    """Deterministic executive summary used when no AI narrative is available."""
    h = s["comparison"]["headline"]
    bullets = []
    if s["comparison"]["warnings"]:
        bullets.append("**Data is incomplete or not comparable:** " + " ".join(s["comparison"]["warnings"]))
    bullets.append(f"Net product sales {usd(h['net_sales']['previous'])} → {usd(h['net_sales']['current'])} "
                   f"({pct(h['net_sales']['change_pct'])}).")
    cp = h["contribution_profit"]
    if cp["current"] is not None and cp["previous"] is not None:
        bullets.append(f"Contribution profit {usd(cp['previous'])} → {usd(cp['current'])} ({pct(cp['change_pct'])}); "
                       f"margin {pct(h['contribution_margin_pct']['previous'], False)} → "
                       f"{pct(h['contribution_margin_pct']['current'], False)}.")
    else:
        bullets.append("Contribution profit is **unavailable** for at least one period (see data-quality warnings).")
    bridge = s["comparison"]["profit_bridge"]
    if bridge.get("available"):
        costs = [st for st in bridge["steps"] if st["profit_impact"] < 0]
        costs.sort(key=lambda st: st["profit_impact"])
        bullets.append("Largest cost increases: " + "; ".join(
            f"{st['label']} {usd(st['change'], True)} ({pct(st['change_pct'])})" for st in costs[:3]) + ".")
    top = s["campaigns"]["campaigns"][0] if s["campaigns"]["campaigns"] else None
    if top and top["spend_change"] > 0 and h["meta_ad_spend"].get("data_complete", True):
        bullets.append(f"Campaign {top['campaign_id']} accounts for {top['share_of_total_spend_change_pct']}% of the "
                       f"Meta spend change ({usd(top['spend_change'], True)}).")
    bullets.append("_Facts only — generated without an AI model. Causes are not inferred; see recommended checks._")
    return bullets


RECOMMENDATION_RULES = {
    "ad_spend_outpaced_sales": "Growth team: review budgets of the campaigns with the largest spend increases against "
                               "blended MER and the 22% ad-spend target; consider a holdout test to measure incrementality.",
    "campaign_spend_increase": "Growth team: inspect daily CPM, frequency and placement data for the flagged campaign(s) in Ads Manager.",
    "fees_per_order_increase": "Operations: reconcile 3PL and carrier invoices with the rate card; check the peak surcharge and "
                               "pick & pack rate change.",
    "contribution_margin_drop": "Finance: confirm the cost sheet and invoices for the period before sharing results.",
    "missing_product_cost": "Finance: add the missing SKU cost(s) to the product-cost sheet, then rebuild the database.",
    "refund_rate_increase": "Customer experience: review refund reasons for the top refunded SKU(s); inspect returned units.",
    "cogs_rate_increase": "Merchandising: review product mix and supplier cost changes behind the higher COGS rate.",
    "platform_value_exceeds_shopify": "Growth team: do not use Meta-reported value as revenue; review attribution settings.",
    "data_completeness": "Data owner: backfill the missing days or re-run the comparison on complete, equal-length periods.",
    "product_margin_drop": "Merchandising: check discounts and unit-cost changes for SKUs whose margin fell.",
    "platform_roas_drop": "Growth team: compare Meta-reported ROAS with Shopify-based blended MER before acting.",
}


# ----------------------------------------------------------------------------- markdown
def render_markdown(s: dict[str, Any], narrative: AnalysisResult | None, chart_files: dict[str, dict[str, Any]],
                    title: str, guardrail_warnings: list[str]) -> str:
    m, c, h = s["meta"], s["comparison"], s["comparison"]["headline"]
    prev, cur = s["previous"], s["current"]
    pl, cl = f"{m['previous_period']['start']} – {m['previous_period']['end']}", f"{m['current_period']['start']} – {m['current_period']['end']}"
    lines = [
        f"# {title}",
        "",
        f"**{m['company']}** · Current period **{cl}** vs. previous **{pl}** · Timezone {m['timezone']} · "
        f"Currency {m['currency']} · Data: {m['data_source']} (scenario `{m['scenario']}`) · Generated {m['generated_at_utc']}",
        "",
        "> Fictional teaching data. Contribution profit = net product sales − COGS − fulfillment − payment fees − "
        "Meta ad spend. It is **not** accounting net income (no other marketing, fixed costs, taxes or overhead).",
        "",
        "## Executive summary",
        "",
    ]
    if narrative and narrative.status == "completed":
        lines += [f"**{narrative.headline}**", "", narrative.answer, ""]
        lines += ["### Findings", "", table(["#", "Type", "Finding", "Evidence"], [
            [i, f.kind.replace("_", " "), f.statement + (f" _(verify: {f.how_to_verify})_" if f.how_to_verify else ""),
             ", ".join(f.evidence_ids)] for i, f in enumerate(narrative.findings, start=1)]), ""]
    else:
        if narrative:
            lines += [f"> AI analysis did not complete ({narrative.status}: {narrative.error or 'no submission'}). "
                      "Showing the facts-only summary.", ""]
        lines += [f"- {b}" for b in facts_only_summary(s)] + [""]

    # Revenue
    ps, cs = prev["sales"], cur["sales"]
    lines += ["## Revenue overview", "", table(["Metric", "Previous", "Current", "Change"], [
        ["Gross product sales", usd(ps["gross_sales"]), usd(cs["gross_sales"]), pct(h["gross_sales"]["change_pct"])],
        ["Discounts", usd(ps["discounts"]), usd(cs["discounts"]), usd(cs["discounts"] - ps["discounts"], True)],
        ["Refunds (by refund date)", usd(ps["refunds"]), usd(cs["refunds"]), usd(cs["refunds"] - ps["refunds"], True)],
        ["**Net product sales**", usd(ps["net_sales"]), usd(cs["net_sales"]), pct(h["net_sales"]["change_pct"])],
        ["Orders", ps["orders"], cs["orders"], pct(h["orders"]["change_pct"])],
        ["Average order value", usd(ps["average_order_value"]), usd(cs["average_order_value"]), ""],
        ["Units", ps["units"], cs["units"], ""],
    ]), "", f"![Daily net sales vs ad spend](charts/{chart_files['sales_vs_ad_spend']['file']})", ""]

    # Profit
    lines += ["## Contribution profit", "", table(["Component", "Previous", "Current", "Change", "% change", "Profit impact"], [
        [st["label"], usd(st["previous"]), usd(st["current"]), usd(st["change"], True), pct(st["change_pct"]),
         usd(st["profit_impact"], True)] for st in c["profit_bridge"].get("steps", [])
    ] + ([["**Contribution profit**", usd(h["contribution_profit"]["previous"]), usd(h["contribution_profit"]["current"]),
           usd(h["contribution_profit"]["change"], True), pct(h["contribution_profit"]["change_pct"]), ""],
          ["Contribution margin", pct(h["contribution_margin_pct"]["previous"], False),
           pct(h["contribution_margin_pct"]["current"], False), "", "", ""]] if c["profit_bridge"].get("available") else [])), ""]
    if c["profit_bridge"].get("available"):
        lines += [f"Bridge check: impacts sum to {usd(c['profit_bridge']['sum_of_impacts'], True)}; profit change "
                  f"{usd(c['profit_bridge']['profit_change'], True)} → reconciles: **{c['profit_bridge']['reconciles']}**.", "",
                  f"![Profit bridge](charts/{chart_files['profit_bridge']['file']})", "",
                  f"![Contribution comparison](charts/{chart_files['contribution_comparison']['file']})", ""]
        cra = c["cost_rate_analysis"]
        lines += ["### Which costs grew faster than sales?", "",
                  f"If every cost had stayed the same share of net sales, profit would have been "
                  f"{usd(cra['profit_if_previous_margin_held'])} instead of {usd(cra['actual_profit'])} "
                  f"(gap {usd(cra['margin_gap'])}).", "",
                  table(["Cost", "% of net sales (prev)", "% of net sales (cur)", "Cost above proportional"], [
                      [r["label"], f"{r['previous_pct_of_net_sales']:.2f}%", f"{r['current_pct_of_net_sales']:.2f}%",
                       usd(r["cost_above_proportional"], True)] for r in cra["components"]]), ""]
    else:
        lines += [f"**Bridge unavailable:** {c['profit_bridge'].get('reason')}", ""]
        for label, r in (("Previous", prev), ("Current", cur)):
            if r["contribution_profit_upper_bound"] is not None:
                lines += [f"- {label} period contribution profit **upper bound** (missing costs excluded, true value "
                          f"is lower): {usd(r['contribution_profit_upper_bound'])}"]
        lines += [""]

    # Advertising
    pa, ca = prev["ads"], cur["ads"]
    lines += ["## Advertising performance (Meta)", "", table(["Metric", "Previous", "Current"], [
        ["Spend", usd(pa["ad_spend"]), usd(ca["ad_spend"])],
        ["Spend as % of net sales", pct(prev["cost_ratios_pct_of_net_sales"]["meta_ad_spend"], False),
         pct(cur["cost_ratios_pct_of_net_sales"]["meta_ad_spend"], False)],
        ["Blended MER (Shopify net sales ÷ spend)", prev["blended_mer"], cur["blended_mer"]],
        ["CPM", usd(pa["cpm"]), usd(ca["cpm"])],
        ["CTR", pct(pa["ctr_pct"], False), pct(ca["ctr_pct"], False)],
        ["Meta-reported purchases", pa["platform_purchases"], ca["platform_purchases"]],
        ["Meta-reported purchase value", usd(pa["platform_purchase_value"]), usd(ca["platform_purchase_value"])],
        ["Meta-reported ROAS", pa["platform_roas"], ca["platform_roas"]],
    ]), "", "> Meta-reported purchases/value are attribution-based and overlap with Shopify orders. They are not "
        "additional revenue and do not prove incrementality.", "",
        table(["Campaign", "Previous spend", "Current spend", "Change", "Share of change", "ROAS (prev → cur, Meta)"], [
            [f"{r['campaign_name_untrusted_text']} (`{r['campaign_id']}`)", usd(r["previous_spend"]), usd(r["current_spend"]),
             usd(r["spend_change"], True), f"{r['share_of_total_spend_change_pct']}%" if r["share_of_total_spend_change_pct"] is not None else "n/a",
             f"{r['previous_platform_roas']} → {r['current_platform_roas']}"] for r in s["campaigns"]["campaigns"]]), "",
        f"![Campaign spend](charts/{chart_files['campaign_spend']['file']})", ""]

    # Products
    lines += ["## Product profitability", "", "Product margin = product net sales − COGS (ads and fulfillment are not "
              "allocated to products).", "", table(["SKU", "Product", "Units (prev → cur)", "Net sales (prev → cur)",
                                                     "Margin % (prev → cur)", "Share of sales (cur)"], [
        [p["sku"], p["title"], f"{p['previous_units']} → {p['current_units']}",
         f"{usd(p['previous_net_sales'])} → {usd(p['current_net_sales'])}",
         f"{pct(p['previous_margin_pct'], False)} → {pct(p['current_margin_pct'], False)}" + ("" if p["cogs_complete"] else " ⚠ missing cost"),
         pct(p["current_share_pct"], False)] for p in s["products"]]), "",
        f"![Product margins](charts/{chart_files['product_margins']['file']})", ""]

    # Refunds and fulfillment
    lines += ["## Refunds and fulfillment costs", "", table(["Fee category", "Previous", "Current", "Change", "Per order (prev → cur)"], [
        [f["category"], usd(f["previous"]), usd(f["current"]), usd(f["change"], True),
         f"{usd(f['previous_per_order'])} → {usd(f['current_per_order'])}"] for f in c["fee_changes"]]), "",
        f"Refund rate (refunds ÷ gross sales): {pct(ps['refund_rate_pct'], False)} → {pct(cs['refund_rate_pct'], False)}. "
        "Refund reasons below are unverified free text from the source system.", "",
        table(["Refund reason (untrusted text)", "Refunds", "Amount"], [
            [r["reason_untrusted_text"], r["refund_count"], usd(r["refunds"])] for r in s["refunds_current"]["by_reason"][:8]]), ""]

    # Anomalies
    lines += ["## Anomalies (rule-based thresholds, not machine learning)", ""]
    lines += [f"- **{a['severity']}** `{a['rule']}` — {a['message']}" for a in s["anomalies"]["anomalies"]] or ["- None flagged."]
    lines += ["", "## Targets (fictional)", "", table(["Target", "Direction", "Target value", "Actual", "Status"], [
        [t["metric"], t["direction"], t["target"], t["actual"], t["status"] + (f" ({t['detail']})" if t["detail"] else "")]
        for t in s["targets"]]), ""]

    # Data quality
    dq = s["data_quality"]
    lines += ["## Data-quality warnings", ""]
    lines += [f"- **{i['severity']}** `{i['check_name']}` ({i['source']}): {i['message']}" for i in dq["ingestion_issues"]] or ["- No ingestion issues recorded."]
    if dq["failed_validation_checks"]:
        lines += [f"- Failed validation checks: {', '.join(dq['failed_validation_checks'])}"]
    for w in c["warnings"]:
        lines += [f"- **comparability** {w}"]
    if narrative and narrative.data_gaps:
        lines += ["", "**Data gaps noted by the analyst:**", ""] + [f"- {g}" for g in narrative.data_gaps]
    lines += [""]

    # Next steps
    lines += ["## Recommended next steps", ""]
    if narrative and narrative.status == "completed" and narrative.recommendations:
        lines += [f"{i}. **{r.action}** — {r.rationale} ({', '.join(r.evidence_ids)})"
                  for i, r in enumerate(narrative.recommendations, start=1)]
    else:
        seen = []
        for a in s["anomalies"]["anomalies"]:
            rec = RECOMMENDATION_RULES.get(a["rule"])
            if rec and rec not in seen:
                seen.append(rec)
        lines += [f"{i}. {r}" for i, r in enumerate(seen, start=1)] or ["1. No rule-based follow-ups triggered."]
    lines += [""]

    if guardrail_warnings:
        lines += ["## Guardrail warnings", "", "Automated checks found these issues in the AI narrative:", ""]
        lines += [f"- {w}" for w in guardrail_warnings] + [""]

    lines += ["## Appendix: sources and method", "",
              "- Sources: Shopify orders/line items/refunds, Meta Insights (daily campaign rows), Google Sheets "
              "(product costs, fulfillment & payment costs, targets), loaded into DuckDB by the ingestion pipeline.",
              "- All figures come from `build_summary()` (SQL views in `sql/views.sql` + `analysis/metrics.py`); "
              "charts plot the same values (see `summary.json → charts`).",
              "- Metric definitions: `knowledge-base/metric-definitions.md`. Reporting rules: `knowledge-base/reporting-rules.md`.",
              ]
    if narrative:
        lines += [f"- AI run: trace `{narrative.trace_id}`, model `{narrative.model}`, {narrative.model_calls} model call(s), "
                  f"{len(narrative.tool_calls)} tool call(s), status `{narrative.status}`.", "",
                  table(["Evidence", "Tool", "Arguments", "OK"], [
                      [t.evidence_id, t.tool, f"`{json.dumps(t.arguments)[:90]}`", "yes" if t.ok else f"no ({t.error_code})"]
                      for t in narrative.tool_calls])]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- consistency
def check_consistency(s: dict[str, Any], chart_data: dict[str, dict[str, Any]]) -> list[str]:
    issues = []
    h = s["comparison"]["headline"]
    bridge = s["comparison"]["profit_bridge"]
    if bridge.get("available"):
        b = chart_data.get("profit_bridge", {})
        if abs(b.get("ends_at", 0) - bridge["current_profit"]) > 0.005:
            issues.append("Bridge chart does not end at current contribution profit.")
        cc = chart_data.get("contribution_comparison", {})
        if cc and abs(cc["current"][-1] - h["contribution_profit"]["current"]) > 0.005:
            issues.append("Contribution chart profit differs from the headline table.")
        if cc and abs(cc["current"][0] - h["net_sales"]["current"]) > 0.005:
            issues.append("Contribution chart net sales differ from the headline table.")
    camp = chart_data.get("campaign_spend", {})
    table_spend = {r["campaign_id"]: r["current_spend"] for r in s["campaigns"]["campaigns"]}
    for cid, v in zip(camp.get("campaign_ids", []), camp.get("current", []), strict=False):
        if abs(table_spend.get(cid, -1) - v) > 0.005:
            issues.append(f"Campaign chart value for {cid} differs from the campaign table.")
    daily = chart_data.get("sales_vs_ad_spend", {})
    cur_days = [d for d in s["daily"] if d["day"] >= s["meta"]["current_period"]["start"]]
    if daily and all(d["net_sales"] is not None for d in cur_days):
        chart_sum = sum(v for d, v in zip(daily["days"], daily["net_sales"], strict=True)
                        if d >= s["meta"]["current_period"]["start"] and v is not None)
        if abs(chart_sum - h["net_sales"]["current"]) > 0.01:
            issues.append("Daily sales chart does not sum to current-period net sales.")
    return issues


# ----------------------------------------------------------------------------- entry point
def generate_report(settings: Settings, *, scenario: str | None = None, previous: Period | None = None,
                    current: Period | None = None, narrative: AnalysisResult | None = None,
                    kind: str = "weekly-review", out_dir: Path | None = None, db_path: Path | None = None) -> ReportOutput:
    previous = previous or settings.previous_week
    current = current or settings.current_week
    s = build_summary(settings, scenario, previous, current, db_path=db_path)
    name = f"{kind}-{s['meta']['scenario']}-{current.start.isoformat()}"
    folder = out_dir or (settings.reports_dir / name)
    chart_dir = folder / "charts"
    labels = (f"Previous ({previous.start:%b %d}–{previous.end:%b %d})", f"Current ({current.start:%b %d}–{current.end:%b %d})")
    sub = f"Northstar Supply (fictional) · {previous.start} to {current.end} · USD"
    raw_p, raw_c = s["_raw"]["previous"], s["_raw"]["current"]
    chart_data: dict[str, dict[str, Any]] = {
        "sales_vs_ad_spend": charts.sales_vs_ad_spend(s["daily"], chart_dir / "sales-vs-ad-spend.png",
                                                       current.start.isoformat(), sub),
        "campaign_spend": charts.campaign_spend(s["campaigns"]["campaigns"], chart_dir / "campaign-spend.png", labels, sub),
        "product_margins": charts.product_margins(s["products"], chart_dir / "product-margins.png", labels, sub),
    }
    if s["comparison"]["profit_bridge"].get("available"):
        chart_data["profit_bridge"] = charts.profit_bridge(s["comparison"]["profit_bridge"], chart_dir / "profit-bridge.png", sub)
        chart_data["contribution_comparison"] = charts.contribution_comparison(
            {k: raw_p[k] for k in ("net", "cogs", "fees", "ads", "profit")},
            {k: raw_c[k] for k in ("net", "cogs", "fees", "ads", "profit")},
            chart_dir / "contribution-comparison.png", labels, sub)
    else:  # charts must not show invented numbers: no bridge without complete data
        chart_data["profit_bridge"] = {"file": None, "skipped": "contribution profit incomplete"}
        chart_data["contribution_comparison"] = {"file": None, "skipped": "contribution profit incomplete"}

    guardrail_warnings = list(narrative.guardrail_warnings) if narrative else []
    if narrative and narrative.status == "completed":
        public_summary = {k: v for k, v in s.items() if not k.startswith("_")}
        known = collect_numbers(public_summary) | collect_numbers(
            [t.output.get("data") for t in narrative.tool_calls if t.output and t.output.get("ok")])
        missing = ungrounded_numbers(narrative.headline + " " + narrative.answer, known)
        if missing:
            guardrail_warnings.append("Narrative numbers not found in report data or tool results: " + ", ".join(missing[:10]))

    consistency = check_consistency(s, chart_data)
    title = "Weekly Business Review" if kind == "weekly-review" else "Business Investigation Report"
    chart_files = {k: {"file": v.get("file") or "missing.png"} for k, v in chart_data.items()}
    md = render_markdown(s, narrative, chart_files, title, guardrail_warnings)
    if not s["comparison"]["profit_bridge"].get("available"):
        md = md.replace("![Profit bridge](charts/missing.png)\n", "").replace("![Contribution comparison](charts/missing.png)\n", "")
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / "report.md"
    md_path.write_text(md, encoding="utf-8")
    payload = {k: v for k, v in s.items() if not k.startswith("_")}
    payload["charts"] = chart_data
    payload["consistency_checks"] = {"passed": not consistency, "issues": consistency}
    payload["guardrail_warnings"] = guardrail_warnings
    if narrative:
        payload["ai_analysis"] = narrative.as_dict()
    json_path = folder / "summary.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    pngs = sorted(chart_dir.glob("*.png"))
    return ReportOutput(folder, md_path, json_path, pngs, consistency)
