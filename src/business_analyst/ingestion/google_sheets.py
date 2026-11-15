"""Google Sheets adapters: mock CSV exports and a read-only Sheets API v4 client.

The finance team keeps three tabs:

* Product Costs   – landed unit cost per SKU with effective dates
* Fulfillment Costs – daily 3PL, carrier and payment-processor charges
* Targets         – fictional business targets

Sheets are typed by people, so expect "$1,234.50", "11/2/2026", lower-case
SKUs and trailing spaces. Normalization fixes the predictable problems and
records an issue for each fix so nothing changes silently.

The live client is UNTESTED against a real spreadsheet (no credentials were
available). Its request/normalization logic is unit-tested with fake responses.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..models import DataQualityIssue
from .common import IngestionError, RawTable, read_csv_table, rows_from_values
from .normalization import clean_text, is_blank, normalize_sku, parse_date, parse_int, parse_money

REQUIRED = {
    "product_costs": ["sku", "unit_cost_usd", "effective_from"],
    "fulfillment_costs": ["date", "cost_category", "amount_usd"],
    "business_targets": ["metric", "target", "direction"],
}
FILES = {"product_costs": "product-costs.csv", "fulfillment_costs": "fulfillment-costs.csv",
         "business_targets": "business-targets.csv"}
COST_CATEGORIES = {"pick_pack", "packaging", "shipping_labels", "payment_processing"}


class MockSheetsAdapter:
    name = "google-sheets:mock"

    def __init__(self, resolve: Callable[[str], Path]):
        self.resolve = resolve

    def fetch(self) -> dict[str, RawTable]:
        return {t: read_csv_table(self.resolve(f"google-sheets/{f}"), t, f"google-sheets/{f}")
                for t, f in FILES.items()}


def make_cost_normalizer(issues: list[DataQualityIssue], source: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def normalize(row: dict[str, Any]) -> dict[str, Any]:
        sku = normalize_sku(row["sku"])
        if sku != str(row["sku"]):
            issues.append(DataQualityIssue(source, "sku_normalized", "info",
                                           f"SKU {row['sku']!r} normalized to {sku!r} to match Shopify.", sku))
        start = parse_date(row["effective_from"])
        end = parse_date(row["effective_to"]) if not is_blank(row.get("effective_to")) else None
        if end and end < start:
            raise ValueError(f"effective_to {end} is before effective_from {start}")
        return {
            "sku": sku,
            "product_name": clean_text(row.get("product_name"), 200),
            "unit_cost": parse_money(row["unit_cost_usd"]),
            "effective_from": start,
            "effective_to": end,
            "notes": clean_text(row.get("notes"), 300),
        }

    return normalize


def normalize_fulfillment(row: dict[str, Any]) -> dict[str, Any]:
    category = str(row["cost_category"]).strip().lower()
    if category not in COST_CATEGORIES:
        raise ValueError(f"unknown cost category {category!r}")
    return {
        "cost_date": parse_date(row["date"]),
        "cost_category": category,
        "amount": parse_money(row["amount_usd"]),
        "orders_covered": parse_int(row["orders_covered"]) if not is_blank(row.get("orders_covered")) else None,
        "source": clean_text(row.get("source"), 100),
        "notes": clean_text(row.get("notes"), 300),
    }


def normalize_target(row: dict[str, Any]) -> dict[str, Any]:
    direction = str(row["direction"]).strip().lower()
    if direction not in {"min", "max"}:
        raise ValueError(f"direction must be min or max, got {direction!r}")
    return {
        "metric": str(row["metric"]).strip().lower(),
        "target_value": float(str(row["target"]).replace("$", "").replace("%", "").replace(",", "")),
        "unit": clean_text(row.get("unit"), 20),
        "direction": direction,
        "period": clean_text(row.get("period"), 20),
        "notes": clean_text(row.get("notes"), 300),
    }


class GoogleSheetsAdapter:
    """Read-only Google Sheets API v4 client using ``spreadsheets.values.get``.

    Authentication (pick one):
    * ``GOOGLE_SERVICE_ACCOUNT_FILE`` – a service-account JSON key; share the
      sheet with the service account's email (Viewer). Needs ``pip install -e ".[live]"``.
    * ``GOOGLE_SHEETS_ACCESS_TOKEN`` – an OAuth access token with the
      ``spreadsheets.readonly`` scope (short-lived; good for experiments).
    """

    name = "google-sheets:api"
    BASE = "https://sheets.googleapis.com/v4/spreadsheets"
    SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

    def __init__(self, spreadsheet_id: str, ranges: dict[str, str], *, access_token: str | None = None,
                 service_account_file: str | None = None, client: Any | None = None):
        if not spreadsheet_id:
            raise IngestionError("Live Google Sheets needs GOOGLE_SHEETS_SPREADSHEET_ID")
        self.spreadsheet_id = spreadsheet_id
        self.ranges = ranges
        self.token = access_token or (self._service_account_token(service_account_file) if service_account_file else None)
        if not self.token:
            raise IngestionError("Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SHEETS_ACCESS_TOKEN for live Google Sheets")
        if client is None:
            import httpx

            client = httpx.Client(timeout=30)
        self.client = client

    def _service_account_token(self, path: str) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:
            raise IngestionError('Service-account auth needs google-auth: pip install -e ".[live]"') from exc
        creds = service_account.Credentials.from_service_account_file(path, scopes=[self.SCOPE])
        creds.refresh(Request())
        return creds.token

    def _values(self, a1_range: str) -> list[list[Any]]:
        url = f"{self.BASE}/{self.spreadsheet_id}/values/{quote(a1_range, safe='')}"
        response = self.client.get(url, headers={"Authorization": f"Bearer {self.token}"},
                                   params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"})
        if response.status_code in (401, 403):
            raise IngestionError(f"Google Sheets denied access (HTTP {response.status_code}). Is the sheet shared?")
        if response.status_code == 400:
            raise IngestionError(f"Google Sheets rejected range {a1_range!r}: {response.text[:200]}")
        response.raise_for_status()
        return response.json().get("values", [])

    def fetch(self) -> dict[str, RawTable]:
        return {name: rows_from_values(self._values(rng), name, f"{self.name}:{rng}") for name, rng in self.ranges.items()}
