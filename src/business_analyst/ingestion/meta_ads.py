"""Meta Ads adapters: mock CSV exports and a read-only Marketing API Insights client.

Normalized into ``ad_campaigns`` (one row per campaign) and
``ad_daily_performance`` (one row per campaign per day).

Source-specific rules (see knowledge-base/data-source-guide.md):

* ``spend`` is a real cost and belongs in contribution profit.
* ``purchases`` / ``purchase_value`` are Meta's *attributed* conversions under
  Meta's attribution settings. They overlap with Shopify orders (the same
  order can be claimed by several channels), so they are stored as
  ``platform_*`` columns and are never added to Shopify sales.
* Insights dates are in the ad account's timezone. This project assumes the
  account timezone equals the reporting timezone; if yours differs, daily
  spend will not line up with Shopify days (see the misaligned_periods scenario).
* Meta returns several purchase action types (``omni_purchase``,
  ``purchase``, ``offsite_conversion.fb_pixel_purchase``) that describe the
  SAME purchases. Pick one — summing them double-counts.

The live client is UNTESTED against a real ad account (no credentials were
available). Its pagination/normalization logic is unit-tested with fake responses.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .common import IngestionError, RawTable, read_csv_table
from .normalization import (
    clean_text,
    is_blank,
    normalize_currency,
    parse_date,
    parse_int,
    parse_money,
)

REQUIRED = {
    "ad_daily_performance": ["date_start", "campaign_id", "spend", "impressions", "clicks", "purchases",
                             "purchase_value", "account_currency"],
    "ad_campaigns": ["campaign_id", "campaign_name"],
}
PURCHASE_ACTION_PRIORITY = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]


class MockMetaAdapter:
    name = "meta:mock"

    def __init__(self, resolve: Callable[[str], Path]):
        self.resolve = resolve

    def fetch(self) -> dict[str, RawTable]:
        return {
            "ad_daily_performance": read_csv_table(self.resolve("meta/campaign-performance.csv"),
                                                   "ad_daily_performance", "meta/campaign-performance.csv"),
            "ad_campaigns": read_csv_table(self.resolve("meta/campaign-metadata.csv"), "ad_campaigns",
                                           "meta/campaign-metadata.csv"),
        }


def normalize_performance(row: dict[str, Any]) -> dict[str, Any]:
    if not is_blank(row.get("date_stop")) and row["date_stop"] != row["date_start"]:
        # Mixing weekly and daily rows would break day-level joins (granularity mismatch).
        raise ValueError(f"row covers {row['date_start']}..{row['date_stop']}; expected one day per row")
    return {
        "perf_date": parse_date(row["date_start"]),
        "campaign_id": str(row["campaign_id"]).strip(),
        "campaign_name": clean_text(row.get("campaign_name"), 200),
        "spend": parse_money(row["spend"]),
        "impressions": parse_int(row["impressions"]),
        "clicks": parse_int(row["clicks"]),
        "platform_purchases": parse_int(row["purchases"]) if not is_blank(row["purchases"]) else None,
        "platform_purchase_value": parse_money(row["purchase_value"]) if not is_blank(row["purchase_value"]) else None,
        "currency": normalize_currency(row["account_currency"]),
    }


def normalize_campaign(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign_id": str(row["campaign_id"]).strip(),
        "campaign_name": clean_text(row["campaign_name"], 200),
        "objective": clean_text(row.get("objective"), 50),
        "status": clean_text(row.get("status"), 20),
        "start_date": parse_date(row["start_date"]) if not is_blank(row.get("start_date")) else None,
        "stop_date": parse_date(row["stop_date"]) if not is_blank(row.get("stop_date")) else None,
        "daily_budget": parse_money(row["daily_budget"]) if not is_blank(row.get("daily_budget")) else None,
        "product_focus": clean_text(row.get("product_focus"), 200),
        "notes": clean_text(row.get("notes"), 500),
    }


def pick_purchase_action(actions: list[dict[str, Any]] | None) -> str:
    """Return the value of the first purchase action type present (never a sum of several)."""
    by_type = {a.get("action_type"): a.get("value") for a in actions or []}
    for action_type in PURCHASE_ACTION_PRIORITY:
        if action_type in by_type:
            return str(by_type[action_type])
    return "0"


class MetaInsightsAdapter:
    """Read-only Meta Marketing API client: campaign metadata + daily campaign Insights.

    Needs a user or system-user access token with the ``ads_read`` permission.
    Only GET requests are made.
    """

    name = "meta:insights"
    BASE = "https://graph.facebook.com"

    def __init__(self, access_token: str, ad_account_id: str, api_version: str, *, client: Any | None = None,
                 max_retries: int = 4, sleep: Callable[[float], None] = time.sleep):
        if not access_token or not ad_account_id:
            raise IngestionError("Live Meta needs META_ACCESS_TOKEN and META_AD_ACCOUNT_ID")
        self.token = access_token
        self.account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self.version = api_version
        self.max_retries = max_retries
        self.sleep = sleep
        if client is None:
            import httpx

            client = httpx.Client(timeout=60)
        self.client = client

    def _get(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            response = self.client.get(url, params=params)
            if response.status_code >= 400:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                # 4/17/32/613/80004 are Meta's rate-limit codes.
                if (error.get("code") in {4, 17, 32, 613, 80004} or response.status_code >= 500) and attempt < self.max_retries:
                    self.sleep(min(5 * 2 ** attempt, 60))
                    continue
                raise IngestionError(f"Meta API error (HTTP {response.status_code}): {error.get('message', response.text[:200])}")
            return response.json()
        raise IngestionError("Meta request kept failing after retries (rate limited or unavailable)")

    def _paginate(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = params
        while next_url:
            payload = self._get(next_url, next_params)
            rows.extend(payload.get("data", []))
            next_url = (payload.get("paging") or {}).get("next")
            next_params = None  # the "next" URL already contains every parameter (including the token)
        return rows

    def fetch(self, start: str, end: str) -> dict[str, RawTable]:
        base = f"{self.BASE}/{self.version}/{self.account}"
        insights = self._paginate(f"{base}/insights", {
            "access_token": self.token,
            "level": "campaign",
            "time_increment": 1,
            "time_range": json.dumps({"since": start, "until": end}),
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,action_values,account_currency",
            "limit": 500,
        })
        campaigns = self._paginate(f"{base}/campaigns", {
            "access_token": self.token,
            "fields": "id,name,objective,effective_status,start_time,stop_time,daily_budget",
            "limit": 200,
        })
        perf = RawTable("ad_daily_performance", self.name, columns=REQUIRED["ad_daily_performance"] + ["date_stop"])
        for r in insights:
            perf.rows.append({
                "date_start": r["date_start"], "date_stop": r.get("date_stop", r["date_start"]),
                "campaign_id": r["campaign_id"], "campaign_name": r.get("campaign_name", ""),
                "spend": r.get("spend", "0"), "impressions": r.get("impressions", "0"), "clicks": r.get("clicks", "0"),
                "purchases": pick_purchase_action(r.get("actions")),
                "purchase_value": pick_purchase_action(r.get("action_values")),
                "account_currency": r.get("account_currency", ""),
            })
        meta = RawTable("ad_campaigns", self.name, columns=REQUIRED["ad_campaigns"] + ["status"])
        for c in campaigns:
            budget = c.get("daily_budget")
            meta.rows.append({
                "campaign_id": c["id"], "campaign_name": c.get("name", ""), "objective": c.get("objective", ""),
                "status": c.get("effective_status", ""), "start_date": (c.get("start_time") or "")[:10],
                "stop_date": (c.get("stop_time") or "")[:10],
                # Meta returns budgets in the currency's minor unit (cents for USD).
                "daily_budget": f"{int(budget) / 100:.2f}" if budget else "", "product_focus": "", "notes": "",
            })
        return {"ad_daily_performance": perf, "ad_campaigns": meta}
