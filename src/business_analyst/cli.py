"""Command-line interface: `python run.py <command>`.

Offline (no API key needed):  doctor, init-db, scenarios, tool, sql, report --no-agent, eval, build-skill
Model-backed (OPENAI_API_KEY): analyze, demo (AI step), report (AI narrative), eval --agent
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from .config import MissingCredentialsError, Period, Settings

PRIMARY_QUESTION = "Our sales increased 20% last week, but contribution profit declined 38%. Why?"
WEEKLY_QUESTION = ("Prepare the weekly business review for last week: what changed, which anomalies matter, "
                   "what data limits confidence, and what should management look at next?")


def _print_header(text: str) -> None:
    print(f"\n{text}\n{'─' * len(text)}")


def _usd(v: Any) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


# ----------------------------------------------------------------------------- doctor
def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    from .database import UnsafeQueryError, connect_readonly, run_readonly_sql
    from .ingestion.loader import build_database

    ok = True

    def check(label: str, passed: bool, detail: str = "", warn_only: bool = False) -> None:
        nonlocal ok
        mark = "PASS" if passed else ("WARN" if warn_only else "FAIL")
        if not passed and not warn_only:
            ok = False
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    _print_header("Northstar AI Business Analyst — setup check")
    check("Python 3.10+", sys.version_info >= (3, 10), sys.version.split()[0])
    for module in ("duckdb", "matplotlib", "openai", "httpx", "dotenv"):
        try:
            __import__(module)
            check(f"package '{module}' installed", True)
        except ImportError:
            check(f"package '{module}' installed", False, 'run: pip install -e ".[dev]"')
    required = ["shopify/products.csv", "shopify/orders.csv", "shopify/order-items.csv", "shopify/refunds.csv",
                "meta/campaign-performance.csv", "meta/campaign-metadata.csv", "google-sheets/product-costs.csv",
                "google-sheets/fulfillment-costs.csv", "google-sheets/business-targets.csv"]
    missing = [f for f in required if not (settings.data_dir / f).exists()]
    check("mock data files present", not missing, ", ".join(missing))
    try:
        report = build_database(settings, None, data_source="mock")
        check("DuckDB database builds from mock data", True,
              f"{report.row_counts.get('orders', 0)} orders, {report.row_counts.get('order_items', 0)} line items")
        errors = report.errors
        check("no data-quality errors in base data", not errors, "; ".join(e.check for e in errors))
        from .analysis.variance_analysis import compare

        con = connect_readonly(report.db_path)
        cmp = compare(con, settings.previous_week, settings.current_week)
        h = cmp["headline"]
        check("net sales +20% (scenario)", h["net_sales"]["change_pct"] == 20.0, _pct(h["net_sales"]["change_pct"]))
        check("Meta ad spend +80% (scenario)", h["meta_ad_spend"]["change_pct"] == 80.0, _pct(h["meta_ad_spend"]["change_pct"]))
        check("contribution profit −38% (scenario)", h["contribution_profit"]["change_pct"] == -38.0,
              _pct(h["contribution_profit"]["change_pct"]))
        check("profit bridge reconciles exactly", cmp["profit_bridge"]["reconciles"] is True)
        try:
            run_readonly_sql(con, "DROP TABLE orders")
            check("SQL tool blocks writes", False, "DROP was not rejected!")
        except UnsafeQueryError:
            check("SQL tool blocks writes", True, "DROP TABLE rejected")
        con.close()
    except Exception as exc:  # noqa: BLE001 - doctor reports everything
        check("DuckDB database builds from mock data", False, f"{type(exc).__name__}: {exc}")
    check("OPENAI_API_KEY set (needed for AI analysis only)", bool(settings.openai_api_key),
          "" if settings.openai_api_key else "offline commands still work", warn_only=True)
    check(f"data source mode: {settings.data_source}", True,
          "live adapters are untested against real accounts" if settings.data_source == "live" else "fictional mock data")
    print("\nAll required checks passed." if ok else "\nSome checks failed — see docs/troubleshooting.md.")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- init-db
def cmd_init_db(settings: Settings, args: argparse.Namespace) -> int:
    from .ingestion.loader import build_database, list_scenarios

    names: list[str | None]
    if args.all:
        names = [None] + [s["name"] for s in list_scenarios(settings) if s["overrides"]]
    else:
        names = [args.scenario]
    for name in names:
        report = build_database(settings, name, data_source=args.source)
        _print_header(f"Built {report.db_path.relative_to(settings.project_root) if report.db_path.is_relative_to(settings.project_root) else report.db_path}")
        print(f"  scenario: {report.scenario}   source: {report.data_source}   timezone: {settings.reporting_timezone}")
        for table, count in report.row_counts.items():
            print(f"  {table:<24} {count:>6} rows")
        if report.issues:
            print("  Data-quality issues:")
            for i in report.issues:
                print(f"   - {i.severity:<7} {i.check}: {i.message} [{i.source}]")
    return 0


def cmd_scenarios(settings: Settings, args: argparse.Namespace) -> int:
    from .ingestion.loader import list_scenarios

    _print_header("Scenarios (use with --scenario NAME)")
    for s in list_scenarios(settings):
        print(f"  {s['name']:<20} {s['title']}")
        print(f"  {'':<20} Q: {s['question']}")
        print(f"  {'':<20} overrides: {', '.join(s['overrides']) or '(base data)'}\n")
    return 0


# ----------------------------------------------------------------------------- printing helpers
def print_reconciliation(settings: Settings, scenario: str | None = None) -> None:
    from .analysis.variance_analysis import campaign_changes, compare
    from .database import connect_readonly
    from .ingestion.loader import ensure_database

    con = connect_readonly(ensure_database(settings, scenario))
    prev, cur = settings.previous_week, settings.current_week
    cmp = compare(con, prev, cur)
    camps = campaign_changes(con, prev, cur)
    con.close()
    h = cmp["headline"]
    _print_header(f"Deterministic numbers (SQL + Python) — {prev.label()} vs {cur.label()}")
    print(f"  {'Metric':<30}{'Previous':>14}{'Current':>14}{'Change':>11}")
    for key, label in [("net_sales", "Net product sales"), ("cogs", "COGS"), ("fees_total", "Fulfillment + payment fees"),
                       ("meta_ad_spend", "Meta advertising spend"), ("contribution_profit", "Contribution profit")]:
        print(f"  {label:<30}{_usd(h[key]['previous']):>14}{_usd(h[key]['current']):>14}{_pct(h[key]['change_pct']):>11}")
    b = cmp["profit_bridge"]
    if b.get("available"):
        _print_header("Profit bridge (each step's impact on contribution profit)")
        for st in b["steps"]:
            print(f"  {st['label']:<30}{st['profit_impact']:>+14,.2f}")
        print(f"  {'= change in profit':<30}{b['profit_change']:>+14,.2f}   reconciles: {b['reconciles']}")
        top = camps["campaigns"][0]
        print(f"\n  Largest spend increase: campaign {top['campaign_id']} {top['spend_change']:+,.2f} "
              f"({top['share_of_total_spend_change_pct']}% of the change)")
    for w in cmp["warnings"]:
        print(f"  ⚠ {w}")


def print_result(result: Any) -> None:
    _print_header(f"AI analysis — {result.status} (trace {result.trace_id})")
    if result.error:
        print(f"  Error: {result.error}")
    print(f"  Tool calls ({len(result.tool_calls)}):")
    for t in result.tool_calls:
        print(f"   {t.evidence_id:>4}  {t.tool:<26} {json.dumps(t.arguments)[:80]}{'' if t.ok else '  ✗ ' + str(t.error_code)}")
    if result.status == "completed":
        print(f"\n  {result.headline}\n")
        print(textwrap.indent(result.answer.strip(), "  "))
        print("\n  Findings:")
        for f in result.findings:
            print(f"   - [{f.kind}] {f.statement} {f.evidence_ids}")
            if f.how_to_verify:
                print(f"       verify: {f.how_to_verify}")
        if result.data_gaps:
            print("\n  Data gaps:")
            for g in result.data_gaps:
                print(f"   - {g}")
        print("\n  Recommendations:")
        for r in result.recommendations:
            print(f"   - {r.action} — {r.rationale} {r.evidence_ids}")
    if result.guardrail_warnings:
        print("\n  Guardrail warnings:")
        for w in result.guardrail_warnings:
            print(f"   - {w}")
    print(f"\n  Model calls: {result.model_calls}, tokens in/out: {result.input_tokens}/{result.output_tokens}")


# ----------------------------------------------------------------------------- analysis commands
def _run_agent(settings: Settings, question: str, scenario: str | None) -> Any:
    from .agent import BusinessAnalystAgent

    settings.require_openai_key()
    print(f"\nAsking the AI analyst ({settings.openai_model}): {question}\n(this usually takes 30–90 seconds)")
    return BusinessAnalystAgent(settings).run(question, scenario=scenario)


def cmd_demo(settings: Settings, args: argparse.Namespace) -> int:
    print("Northstar Supply (fictional) — sales are up, profit is down.")
    print_reconciliation(settings)
    if not settings.openai_api_key:
        print("\nNo OPENAI_API_KEY found, so the AI investigation step was skipped.")
        print("Everything above was computed offline. Add a key to .env, then run:\n"
              "  python run.py analyze --scenario profit_decline")
        from .reporting.reports import generate_report

        out = generate_report(settings, kind="weekly-review")
        print(f"\nFacts-only report written to {out.markdown.relative_to(settings.project_root)}")
        return 0
    result = _run_agent(settings, PRIMARY_QUESTION, None)
    print_result(result)
    from .reporting.reports import generate_report

    out = generate_report(settings, narrative=result, kind="investigation")
    print(f"\nReport: {out.markdown.relative_to(settings.project_root)}  (charts in {out.folder.name}/charts)")
    return 0 if result.status == "completed" else 1


def cmd_analyze(settings: Settings, args: argparse.Namespace) -> int:
    from .ingestion.loader import load_scenario

    scenario = load_scenario(settings, args.scenario)
    question = args.question or scenario.get("question") or PRIMARY_QUESTION
    print(f"Scenario: {scenario['name']} — {scenario.get('title', 'base data')}")
    result = _run_agent(settings, question, args.scenario)
    print_result(result)
    if not args.no_report:
        from .reporting.reports import generate_report

        out = generate_report(settings, scenario=args.scenario, narrative=result, kind="investigation")
        print(f"\nReport: {out.markdown.relative_to(settings.project_root)}")
    return 0 if result.status == "completed" else 1


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    from .reporting.reports import generate_report

    previous = Period.parse(args.previous_start, args.previous_end) if args.previous_start else None
    current = Period.parse(args.current_start, args.current_end) if args.current_start else None
    narrative = None
    if not args.no_agent:
        if settings.openai_api_key:
            narrative = _run_agent(settings, WEEKLY_QUESTION, args.scenario)
            print_result(narrative)
        else:
            print("No OPENAI_API_KEY: writing the facts-only report (use --no-agent to hide this note).")
    out = generate_report(settings, scenario=args.scenario, previous=previous, current=current, narrative=narrative)
    _print_header("Report written")
    print(f"  Markdown: {out.markdown.relative_to(settings.project_root)}")
    print(f"  JSON:     {out.summary_json.relative_to(settings.project_root)}")
    for p in out.charts:
        print(f"  Chart:    {p.relative_to(settings.project_root)}")
    print(f"  Chart/table consistency: {'passed' if not out.consistency_issues else out.consistency_issues}")
    return 0 if not out.consistency_issues else 1


# ----------------------------------------------------------------------------- learning tools
def cmd_tool(settings: Settings, args: argparse.Namespace) -> int:
    from .database import connect_readonly
    from .ingestion.loader import ensure_database
    from .tools.registry import ToolContext, build_registry

    registry = build_registry()
    if args.name == "list":
        for spec in registry.specs.values():
            print(f"  {spec.name:<28} {spec.description[:90]}")
        return 0
    con = connect_readonly(ensure_database(settings, args.scenario))
    output = registry.execute(args.name, args.args or "{}", ToolContext(con=con, settings=settings,
                                                                         scenario=args.scenario or "base"))
    con.close()
    print(json.dumps(output, indent=2, default=str))
    return 0 if output.get("ok") else 1


def cmd_sql(settings: Settings, args: argparse.Namespace) -> int:
    from .database import QueryTimeoutError, UnsafeQueryError, connect_readonly, run_readonly_sql
    from .ingestion.loader import ensure_database

    con = connect_readonly(ensure_database(settings, args.scenario))
    try:
        result = run_readonly_sql(con, args.query, row_limit=settings.sql_row_limit,
                                  timeout_seconds=settings.sql_timeout_seconds)
    except (UnsafeQueryError, QueryTimeoutError) as exc:
        print(f"Rejected: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"SQL error: {str(exc).splitlines()[0]}")
        return 1
    finally:
        con.close()
    widths = [max(len(str(c)), *(len(str(r[i])) for r in result["rows"])) if result["rows"] else len(str(c))
              for i, c in enumerate(result["columns"])]
    print("  ".join(str(c).ljust(w) for c, w in zip(result["columns"], widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for row in result["rows"]:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths, strict=True)))
    print(f"({result['row_count']} row(s){', truncated at ' + str(result['row_limit']) if result['truncated'] else ''})")
    return 0


def cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    from .evaluation import run_evaluations

    return run_evaluations(settings, include_agent=args.agent, case_filter=args.case, verbose=args.verbose)


def cmd_build_skill(settings: Settings, args: argparse.Namespace) -> int:
    from .skill_builder import build_skill

    path = build_skill(settings)
    print(f"Built {path.relative_to(settings.project_root)}")
    return 0


# ----------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python run.py", description="Northstar Supply AI Business Analyst (fictional data).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check your setup (offline)")
    p = sub.add_parser("init-db", help="Build the DuckDB database from source data (offline for mock data)")
    p.add_argument("--scenario", help="Scenario name (see `scenarios`); default: base data")
    p.add_argument("--all", action="store_true", help="Build the base database and every scenario database")
    p.add_argument("--source", choices=["mock", "live"], default=None, help="Override DATA_SOURCE")
    sub.add_parser("scenarios", help="List teaching scenarios")
    sub.add_parser("demo", help="Run the main sales-up/profit-down scenario")

    p = sub.add_parser("analyze", help="Ask the AI analyst a question (needs OPENAI_API_KEY)")
    p.add_argument("--scenario", help="Scenario name; its question is used unless --question is given")
    p.add_argument("--question", help="Your own business question")
    p.add_argument("--no-report", action="store_true", help="Do not write a report")

    p = sub.add_parser("report", help="Generate the weekly business review (Markdown + JSON + charts)")
    p.add_argument("--scenario")
    p.add_argument("--no-agent", action="store_true", help="Facts-only report without the AI narrative")
    for name in ("previous-start", "previous-end", "current-start", "current-end"):
        p.add_argument(f"--{name}", help="Custom period date (YYYY-MM-DD); give all four")

    p = sub.add_parser("eval", help="Run evaluations (deterministic by default)")
    p.add_argument("--agent", action="store_true", help="Also run model-backed scenario evaluations (needs key)")
    p.add_argument("--case", help="Run only cases whose id contains this text")
    p.add_argument("--verbose", action="store_true")

    sub.add_parser("build-skill", help="Build claude-skill/business-analyst-skill.zip")

    p = sub.add_parser("tool", help="Run one analytical tool directly (offline). `tool list` lists them")
    p.add_argument("name")
    p.add_argument("--args", help='JSON arguments, e.g. \'{"start_date":"2026-11-02","end_date":"2026-11-08"}\'')
    p.add_argument("--scenario")

    p = sub.add_parser("sql", help="Run a read-only SQL query against the database (offline)")
    p.add_argument("query")
    p.add_argument("--scenario")
    return parser


COMMANDS = {
    "doctor": cmd_doctor, "init-db": cmd_init_db, "scenarios": cmd_scenarios, "demo": cmd_demo,
    "analyze": cmd_analyze, "report": cmd_report, "eval": cmd_eval, "build-skill": cmd_build_skill,
    "tool": cmd_tool, "sql": cmd_sql,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report" and any([args.previous_start, args.current_start]) and not all(
            [args.previous_start, args.previous_end, args.current_start, args.current_end]):
        print("Give all four of --previous-start --previous-end --current-start --current-end.")
        return 2
    try:
        settings = Settings.from_env()
        return COMMANDS[args.command](settings, args)
    except MissingCredentialsError as exc:
        print(f"\n{exc}")
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # friendly message instead of a traceback for common errors
        from .database import DatabaseNotInitializedError
        from .ingestion.common import IngestionError

        if isinstance(exc, (IngestionError, DatabaseNotInitializedError, ValueError)):
            print(f"\nError: {exc}")
            return 1
        raise
