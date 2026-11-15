"""Build the DuckDB analytical database from Shopify, Meta and Google Sheets sources.

Pipeline (the same for mock and live sources):

    1. fetch      adapters return RawTables (rows of strings, normalized headers)
    2. validate   required columns present? (missing column = stop, loudly)
    3. normalize  parse money/dates/SKUs/currencies; bad rows are skipped AND recorded
    4. dedupe     drop duplicate keys; record what was dropped
    5. scan       flag instruction-like text in free-text fields (prompt injection)
    6. load       write a NEW database file: schema -> rows -> views
    7. check      run sql/validation-queries.sql and store every issue found
    8. swap       replace the old database file only after everything succeeded

The loader is the only code that ever opens the database for writing.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import apply_sql_file, connect_writable, fetch_dicts, load_validation_queries, table_row_counts
from ..models import DataQualityIssue
from . import google_sheets, meta_ads, shopify
from .common import IngestionError, RawTable, dedupe, normalize_rows, require_columns, scan_free_text

TABLE_COLUMNS = {
    "products": ["product_id", "sku", "title", "category", "list_price", "status"],
    "orders": ["order_id", "order_name", "created_at_utc", "order_date", "customer_id", "currency",
               "financial_status", "fulfillment_status", "discount_codes", "subtotal_price", "total_discounts",
               "shipping_charged", "total_tax", "total_price", "source_name", "payment_gateway"],
    "order_items": ["line_item_id", "order_id", "product_id", "sku", "product_title", "quantity", "unit_price",
                    "gross_amount", "discount_amount", "net_amount"],
    "refunds": ["refund_id", "order_id", "line_item_id", "processed_at_utc", "refund_date", "quantity",
                "refund_amount", "restocked", "reason", "currency"],
    "product_costs": ["sku", "product_name", "unit_cost", "effective_from", "effective_to", "notes"],
    "fulfillment_costs": ["cost_date", "cost_category", "amount", "orders_covered", "source", "notes"],
    "ad_campaigns": ["campaign_id", "campaign_name", "objective", "status", "start_date", "stop_date",
                     "daily_budget", "product_focus", "notes"],
    "ad_daily_performance": ["perf_date", "campaign_id", "campaign_name", "spend", "impressions", "clicks",
                             "platform_purchases", "platform_purchase_value", "currency"],
    "business_targets": ["metric", "target_value", "unit", "direction", "period", "notes"],
}


@dataclass
class LoadReport:
    db_path: Path
    scenario: str
    data_source: str
    row_counts: dict[str, int] = field(default_factory=dict)
    issues: list[DataQualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "warning"]


# ----------------------------------------------------------------------------- scenarios
def list_scenarios(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for manifest in sorted(settings.scenarios_dir.glob("*/scenario.json")):
        out.append(json.loads(manifest.read_text(encoding="utf-8")))
    return out


def load_scenario(settings: Settings, name: str | None) -> dict[str, Any]:
    if not name or name == "base":
        return {"name": "base", "overrides": [], "question": None}
    manifest = settings.scenarios_dir / name / "scenario.json"
    if not manifest.exists():
        names = ", ".join(s["name"] for s in list_scenarios(settings))
        raise IngestionError(f"Unknown scenario {name!r}. Available: {names}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def scenario_db_path(settings: Settings, name: str | None) -> Path:
    """Scenarios without file overrides share the base database."""
    scenario = load_scenario(settings, name)
    return settings.db_path(None if not scenario["overrides"] else scenario["name"])


def make_resolver(settings: Settings, scenario: dict[str, Any]):
    overrides = set(scenario.get("overrides", []))

    def resolve(relative: str) -> Path:
        if relative in overrides:
            return settings.scenarios_dir / scenario["name"] / relative
        return settings.data_dir / relative

    return resolve


# ----------------------------------------------------------------------------- fetch
def fetch_mock(settings: Settings, scenario: dict[str, Any]) -> dict[str, RawTable]:
    resolve = make_resolver(settings, scenario)
    tables: dict[str, RawTable] = {}
    for adapter in (shopify.MockShopifyAdapter(resolve), meta_ads.MockMetaAdapter(resolve),
                    google_sheets.MockSheetsAdapter(resolve)):
        tables.update(adapter.fetch())
    return tables


def fetch_live(settings: Settings, issues: list[DataQualityIssue]) -> dict[str, RawTable]:  # pragma: no cover - needs credentials
    start, end = settings.live_start_date, settings.live_end_date
    tables: dict[str, RawTable] = {}
    tables.update(shopify.ShopifyGraphQLAdapter(
        settings.shopify_store_domain or "", settings.shopify_access_token or "", settings.shopify_api_version,
    ).fetch(start, end, issues))
    tables.update(meta_ads.MetaInsightsAdapter(
        settings.meta_access_token or "", settings.meta_ad_account_id or "", settings.meta_api_version,
    ).fetch(start, end))
    tables.update(google_sheets.GoogleSheetsAdapter(
        settings.google_spreadsheet_id or "", settings.google_ranges, access_token=settings.google_access_token,
        service_account_file=settings.google_service_account_file,
    ).fetch())
    return tables


# ----------------------------------------------------------------------------- normalize
def normalize_all(tables: dict[str, RawTable], tz: str, issues: list[DataQualityIssue]) -> dict[str, list[dict]]:
    required = {**shopify.REQUIRED, **meta_ads.REQUIRED, **google_sheets.REQUIRED}
    for name, table in tables.items():
        require_columns(table, required[name])

    t = tables
    out: dict[str, list[dict]] = {}
    out["products"] = dedupe(normalize_rows(t["products"], shopify.normalize_product, issues, "sku"),
                             lambda r: r["sku"], t["products"].source, issues)
    out["orders"] = dedupe(normalize_rows(t["orders"], shopify.make_order_normalizer(tz), issues, "order_id"),
                           lambda r: r["order_id"], t["orders"].source, issues)
    out["order_items"] = dedupe(normalize_rows(t["order_items"], shopify.normalize_order_item, issues, "line_item_id"),
                                lambda r: r["line_item_id"], t["order_items"].source, issues)
    out["refunds"] = dedupe(normalize_rows(t["refunds"], shopify.make_refund_normalizer(tz), issues, "refund_id"),
                            lambda r: r["refund_id"], t["refunds"].source, issues)
    cost_src = t["product_costs"].source
    out["product_costs"] = dedupe(
        normalize_rows(t["product_costs"], google_sheets.make_cost_normalizer(issues, cost_src), issues, "sku"),
        lambda r: (r["sku"], r["effective_from"]), cost_src, issues)
    out["fulfillment_costs"] = dedupe(
        normalize_rows(t["fulfillment_costs"], google_sheets.normalize_fulfillment, issues, "date"),
        lambda r: (r["cost_date"], r["cost_category"]), t["fulfillment_costs"].source, issues)
    out["business_targets"] = dedupe(
        normalize_rows(t["business_targets"], google_sheets.normalize_target, issues, "metric"),
        lambda r: r["metric"], t["business_targets"].source, issues)
    out["ad_campaigns"] = dedupe(normalize_rows(t["ad_campaigns"], meta_ads.normalize_campaign, issues, "campaign_id"),
                                 lambda r: r["campaign_id"], t["ad_campaigns"].source, issues)
    out["ad_daily_performance"] = dedupe(
        normalize_rows(t["ad_daily_performance"], meta_ads.normalize_performance, issues, "campaign_id"),
        lambda r: (r["perf_date"], r["campaign_id"]), t["ad_daily_performance"].source, issues)

    scan_free_text(out["ad_campaigns"], ["campaign_name", "notes", "product_focus"], t["ad_campaigns"].source,
                   "campaign_id", issues)
    scan_free_text(out["refunds"], ["reason"], t["refunds"].source, "refund_id", issues)
    scan_free_text(out["products"], ["title"], t["products"].source, "sku", issues)
    return out


# ----------------------------------------------------------------------------- load
def _csv_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _insert(con, table: str, rows: list[dict[str, Any]], workdir: Path) -> None:
    """Bulk-load normalized rows: write a temporary CSV, then COPY it into the table.

    (Row-by-row INSERTs work too, but COPY is ~50x faster in DuckDB.)
    """
    if not rows:
        return
    cols = TABLE_COLUMNS[table]
    staging = workdir / f"{table}.csv"
    with staging.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for r in rows:
            writer.writerow(["" if (v := _csv_value(r.get(c))) is None else v for c in cols])
    con.execute(f"COPY {table} ({', '.join(cols)}) FROM '{staging.as_posix()}' (HEADER, NULLSTR '')")
    staging.unlink()


def build_database(
    settings: Settings,
    scenario_name: str | None = None,
    *,
    data_source: str | None = None,
    db_path: Path | None = None,
) -> LoadReport:
    source = data_source or settings.data_source
    scenario = load_scenario(settings, scenario_name)
    if source == "live" and scenario["overrides"]:
        raise IngestionError("Scenarios only apply to mock data. Use --source mock with --scenario.")
    target = db_path or settings.db_path(None if not scenario["overrides"] else scenario["name"])
    issues: list[DataQualityIssue] = []

    raw = fetch_live(settings, issues) if source == "live" else fetch_mock(settings, scenario)
    rows = normalize_all(raw, settings.reporting_timezone, issues)

    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:6]}.tmp")
    con = connect_writable(tmp)
    try:
        apply_sql_file(con, settings.sql_dir / "schema.sql")
        with tempfile.TemporaryDirectory() as workdir:
            for table in TABLE_COLUMNS:
                _insert(con, table, rows[table], Path(workdir))
        apply_sql_file(con, settings.sql_dir / "views.sql")
        for check in load_validation_queries(settings.sql_dir / "validation-queries.sql"):
            found = fetch_dicts(con, check.sql)
            if found:
                sample = "; ".join(", ".join(f"{k}={v}" for k, v in r.items()) for r in found[:3])
                issues.append(DataQualityIssue("validation-queries.sql", check.name, check.severity,  # type: ignore[arg-type]
                                               f"{check.description} ({len(found)} row(s)).", sample[:500], len(found)))
        for i, issue in enumerate(issues, start=1):
            con.execute("INSERT INTO data_quality_issues VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [i, issue.source, issue.check, issue.severity, issue.message, issue.record_ref, issue.count])
        counts = table_row_counts(con)
        con.execute("INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, ?, ?)", [
            uuid.uuid4().hex[:12], datetime.now(timezone.utc).replace(tzinfo=None), source, scenario["name"],
            settings.reporting_timezone, json.dumps(counts)])
        con.execute("CHECKPOINT")
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, target)  # atomic swap: a failed build never destroys the previous database
    return LoadReport(target, scenario["name"], source, counts, issues)


def database_is_stale(settings: Settings, path: Path) -> bool:
    """True if any source file, scenario file or SQL file changed after the database was built."""
    if not path.exists():
        return True
    built = path.stat().st_mtime
    watched = [*settings.data_dir.rglob("*.csv"), *settings.data_dir.rglob("scenario.json"), *settings.sql_dir.glob("*.sql")]
    return any(f.stat().st_mtime > built for f in watched)


def ensure_database(settings: Settings, scenario_name: str | None = None) -> Path:
    """Build (or rebuild) the database for a scenario if it is missing or older than its sources.

    Live databases are never rebuilt automatically (that would call external APIs).
    """
    path = scenario_db_path(settings, scenario_name)
    if not path.exists() or (settings.data_source == "mock" and database_is_stale(settings, path)):
        build_database(settings, scenario_name)
    return path
