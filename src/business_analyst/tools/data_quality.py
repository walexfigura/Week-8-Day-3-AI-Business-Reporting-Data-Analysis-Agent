"""Data-quality, schema, knowledge and read-only SQL tools."""

from __future__ import annotations

import re
from typing import Any

from ..analysis.metrics import source_coverage
from ..database import fetch_dicts, load_validation_queries, run_readonly_sql
from .registry import NULLABLE_DATE, ToolContext, ToolInputError, ToolSpec, period_arg


def get_data_quality_report(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    con = ctx.require_con()
    stored = fetch_dicts(con, """
        SELECT source, check_name, severity, message, record_ref, issue_count
        FROM data_quality_issues ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, issue_id""")
    live = []
    for check in load_validation_queries(ctx.settings.sql_dir / "validation-queries.sql"):
        rows = fetch_dicts(con, check.sql)
        live.append({"check": check.name, "severity": check.severity, "description": check.description,
                     "passed": not rows, "failing_rows": len(rows)})
    result: dict[str, Any] = {
        "ingestion_issues": [{**r, "message": r["message"], "record_ref_untrusted_text": r.pop("record_ref")} for r in stored],
        "validation_checks": live,
        "summary": {
            "errors": sum(1 for r in stored if r["severity"] == "error"),
            "warnings": sum(1 for r in stored if r["severity"] == "warning"),
            "failed_checks": [c["check"] for c in live if not c["passed"]],
        },
    }
    if args.get("start_date") and args.get("end_date"):
        period = period_arg(args)
        result["coverage"] = {"period": period.as_dict(), **source_coverage(con, period)}
    else:
        result["coverage_hint"] = "Pass start_date and end_date to check which days each source covers."
    run = fetch_dicts(con, "SELECT data_source, scenario, reporting_tz, loaded_at_utc FROM ingestion_runs")
    if run:
        result["database_build"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in run[0].items()}
    return result


TABLE_NOTES = {
    "orders": "one row per Shopify order; subtotal_price is after discounts; total_price includes shipping+tax",
    "order_items": "one row per order line; net_amount = gross_amount - discount_amount",
    "refunds": "one row per refund line; refund_date decides the reporting period",
    "product_costs": "unit cost per SKU with effective_from/effective_to dates",
    "fulfillment_costs": "one row per day per cost category (pick_pack, packaging, shipping_labels, payment_processing)",
    "ad_daily_performance": "one row per Meta campaign per day; platform_* = Meta-attributed results",
    "v_daily_contribution": "one row per day with every contribution-profit component (pre-aggregated)",
}


def get_available_tables(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    con = ctx.require_con()
    rows = fetch_dicts(con, """
        SELECT t.table_name, t.table_type, string_agg(c.column_name || ' ' || c.data_type, ', ' ORDER BY c.ordinal_position) AS columns
        FROM information_schema.tables AS t
        JOIN information_schema.columns AS c ON c.table_name = t.table_name AND c.table_schema = t.table_schema
        WHERE t.table_schema = 'main'
        GROUP BY t.table_name, t.table_type ORDER BY t.table_type, t.table_name""")
    tables = []
    for r in rows:
        entry = {"name": r["table_name"], "kind": "view" if r["table_type"] == "VIEW" else "table", "columns": r["columns"]}
        if entry["kind"] == "table":
            entry["row_count"] = con.execute(f'SELECT COUNT(*) FROM "{r["table_name"]}"').fetchone()[0]
        if r["table_name"] in TABLE_NOTES:
            entry["grain_and_notes"] = TABLE_NOTES[r["table_name"]]
        tables.append(entry)
    return {"tables": tables, "reporting_timezone": ctx.settings.reporting_timezone, "currency": "USD",
            "tip": "Prefer the purpose-built tools. Use run_readonly_sql only for questions they cannot answer."}


def run_sql(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str):
        raise ToolInputError("'query' must be a SQL string")
    result = run_readonly_sql(ctx.require_con(), query, row_limit=ctx.settings.sql_row_limit,
                              timeout_seconds=ctx.settings.sql_timeout_seconds)
    result["reminder"] = ("Aggregate each table to a common grain before joining money columns, or totals will be "
                          "double-counted.")
    return result


# ----------------------------------------------------------------------------- knowledge
def _sections(markdown: str, level: str = "### ") -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in markdown.splitlines():
        if line.startswith(level):
            current = line[len(level):].strip()
            out[current] = ""
        elif line.startswith("## ") and current:
            current = None
        elif current:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def explain_metric_definition(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("metric_name") or "").strip()
    if not name:
        raise ToolInputError("'metric_name' is required, e.g. 'contribution profit'")
    sections = _sections((ctx.settings.knowledge_dir / "metric-definitions.md").read_text(encoding="utf-8"))
    wanted = _slug(name)
    for title, body in sections.items():
        if wanted == _slug(title) or wanted in _slug(title) or _slug(title) in wanted:
            return {"metric": title, "definition": body, "source": "knowledge-base/metric-definitions.md"}
    words = set(wanted.split())
    scored = sorted(sections, key=lambda t: len(words & set(_slug(t).split())), reverse=True)
    if scored and words & set(_slug(scored[0]).split()):
        return {"metric": scored[0], "definition": sections[scored[0]], "source": "knowledge-base/metric-definitions.md",
                "note": f"Closest match for {name!r}."}
    raise ToolInputError(f"No definition for {name!r}. Known metrics: {', '.join(sections)}")


def get_business_targets(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    rows = fetch_dicts(ctx.require_con(), "SELECT metric, target_value, unit, direction, period, notes FROM business_targets ORDER BY metric")
    return {"targets": rows, "note": "All targets are FICTIONAL teaching values for Northstar Supply."}


def read_workflow(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    folder = ctx.settings.agent_dir / "workflows"
    available = sorted(p.stem for p in folder.glob("*.md"))
    name = str(args.get("workflow") or "").strip().removesuffix(".md")
    if name not in available:
        raise ToolInputError(f"Unknown workflow {name!r}. Available: {', '.join(available)}")
    return {"workflow": name, "instructions": (folder / f"{name}.md").read_text(encoding="utf-8")}


def _workflow_names() -> list[str]:
    from ..config import find_project_root

    try:
        return sorted(p.stem for p in (find_project_root() / "agent" / "workflows").glob("*.md"))
    except RuntimeError:  # pragma: no cover
        return []


SPECS = [
    ToolSpec(
        name="get_data_quality_report",
        description=("Data-quality issues recorded during ingestion (duplicates removed, malformed rows, normalized "
                     "SKUs, instruction-like text), live validation checks, and (with dates) which days each "
                     "source covers. Check this before drawing conclusions."),
        properties={"start_date": NULLABLE_DATE, "end_date": NULLABLE_DATE},
        handler=get_data_quality_report, category="data_quality",
    ),
    ToolSpec(
        name="get_available_tables",
        description="List database tables and views with columns, row counts and grain notes.",
        properties={},
        handler=get_available_tables, category="data_quality",
    ),
    ToolSpec(
        name="run_readonly_sql",
        description=("Run ONE read-only SELECT against the analytical database (row limit and timeout enforced; "
                     "writes, file access and unknown tables are rejected). Use only when no other tool fits."),
        properties={"query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement"}},
        handler=run_sql, category="data_quality",
    ),
    ToolSpec(
        name="explain_metric_definition",
        description="Return the official definition, formula, required data, missing-value rule and caveats for a metric.",
        properties={"metric_name": {"type": "string", "description": "e.g. 'net product sales', 'ROAS'"}},
        handler=explain_metric_definition, category="knowledge",
    ),
    ToolSpec(
        name="get_business_targets",
        description="Fictional business targets (margin, ad efficiency, refund rate, cost thresholds).",
        properties={},
        handler=get_business_targets, category="knowledge",
    ),
    ToolSpec(
        name="read_workflow",
        description="Load detailed step-by-step instructions for an analysis workflow before starting it.",
        properties={"workflow": {"type": "string", "enum": _workflow_names() or ["investigate-profit-decline"],
                                 "description": "Workflow name"}},
        handler=read_workflow, category="knowledge",
    ),
]
