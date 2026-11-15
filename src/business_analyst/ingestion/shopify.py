"""Shopify adapters: mock CSV exports and a read-only GraphQL Admin API client.

Both adapters return the same ``RawTable`` shapes, and ``normalize_*`` turns
them into rows for the ``products``, ``orders``, ``order_items`` and
``refunds`` tables.

Source-specific rules documented in knowledge-base/data-source-guide.md:

* ``subtotal_price`` is already NET of discounts. Line items carry the same
  discounts as ``total_discount``. Subtract discounts once — from the lines.
* ``total_price`` includes shipping and tax, so it is not product sales.
* Timestamps are UTC. The reporting date is computed in the reporting timezone.
* A refund belongs to the period of its ``processed_at`` date.

The live client is UNTESTED against a real store (no credentials were
available while building this project). Its request/pagination/normalization
logic is unit-tested with recorded-shape fake responses only.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import DataQualityIssue
from .common import IngestionError, RawTable, read_csv_table
from .normalization import (
    clean_text,
    normalize_currency,
    normalize_sku,
    parse_bool,
    parse_int,
    parse_money,
    parse_timestamp_utc,
    to_reporting_date,
)

REQUIRED = {
    "products": ["product_id", "sku", "title"],
    "orders": ["order_id", "created_at", "currency", "subtotal_price", "total_discounts",
               "shipping_charged", "total_tax", "total_price"],
    "order_items": ["line_item_id", "order_id", "sku", "quantity", "unit_price", "total_discount"],
    "refunds": ["refund_id", "order_id", "processed_at", "refund_amount", "currency"],
}
FILES = {"products": "products.csv", "orders": "orders.csv", "order_items": "order-items.csv",
         "refunds": "refunds.csv"}


class MockShopifyAdapter:
    """Reads the fictional CSV exports in data/shopify/ (or scenario overrides)."""

    name = "shopify:mock"

    def __init__(self, resolve: Callable[[str], Path]):
        self.resolve = resolve  # maps "shopify/orders.csv" -> actual path (base or scenario override)

    def fetch(self) -> dict[str, RawTable]:
        return {t: read_csv_table(self.resolve(f"shopify/{f}"), t, f"shopify/{f}") for t, f in FILES.items()}


# ----------------------------------------------------------------------------- normalization
def normalize_product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": str(row["product_id"]).strip(),
        "sku": normalize_sku(row["sku"]),
        "title": clean_text(row["title"], 200),
        "category": clean_text(row.get("category"), 100),
        "list_price": parse_money(row["list_price"]) if row.get("list_price") else None,
        "status": clean_text(row.get("status"), 30),
    }


def make_order_normalizer(tz: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def normalize(row: dict[str, Any]) -> dict[str, Any]:
        created = parse_timestamp_utc(row["created_at"])
        return {
            "order_id": parse_int(row["order_id"]),
            "order_name": clean_text(row.get("order_name"), 40),
            "created_at_utc": created.replace(tzinfo=None),
            "order_date": to_reporting_date(created, tz),
            "customer_id": clean_text(row.get("customer_id"), 40),
            "currency": normalize_currency(row["currency"]),
            "financial_status": clean_text(row.get("financial_status"), 30),
            "fulfillment_status": clean_text(row.get("fulfillment_status"), 30),
            "discount_codes": clean_text(row.get("discount_codes"), 100),
            "subtotal_price": parse_money(row["subtotal_price"]),
            "total_discounts": parse_money(row["total_discounts"]),
            "shipping_charged": parse_money(row["shipping_charged"]),
            "total_tax": parse_money(row["total_tax"]),
            "total_price": parse_money(row["total_price"]),
            "source_name": clean_text(row.get("source_name"), 30),
            "payment_gateway": clean_text(row.get("payment_gateway"), 40),
        }

    return normalize


def normalize_order_item(row: dict[str, Any]) -> dict[str, Any]:
    quantity = parse_int(row["quantity"])
    if quantity == 0:
        raise ValueError("quantity is zero")
    unit_price = parse_money(row["unit_price"])
    discount = parse_money(row["total_discount"])
    gross = unit_price * quantity
    if discount > gross:
        raise ValueError(f"discount {discount} exceeds line value {gross}")
    return {
        "line_item_id": parse_int(row["line_item_id"]),
        "order_id": parse_int(row["order_id"]),
        "product_id": str(row.get("product_id") or "").strip() or None,
        "sku": normalize_sku(row["sku"]),
        "product_title": clean_text(row.get("product_title"), 200),
        "quantity": quantity,
        "unit_price": unit_price,
        "gross_amount": gross,
        "discount_amount": discount,
        "net_amount": gross - discount,
    }


def make_refund_normalizer(tz: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def normalize(row: dict[str, Any]) -> dict[str, Any]:
        processed = parse_timestamp_utc(row["processed_at"])
        line = row.get("line_item_id")
        return {
            "refund_id": parse_int(row["refund_id"]),
            "order_id": parse_int(row["order_id"]),
            "line_item_id": parse_int(line) if line not in (None, "") else None,
            "processed_at_utc": processed.replace(tzinfo=None),
            "refund_date": to_reporting_date(processed, tz),
            "quantity": parse_int(row["quantity"]) if row.get("quantity") not in (None, "") else None,
            "refund_amount": parse_money(row["refund_amount"]),
            "restocked": parse_bool(row.get("restocked")),
            "reason": clean_text(row.get("reason"), 300),
            "currency": normalize_currency(row["currency"]),
        }

    return normalize


# ----------------------------------------------------------------------------- live adapter
_GID_NUM = re.compile(r"(\d+)$")

ORDERS_QUERY = """
query NorthstarOrders($cursor: String, $search: String!) {
  orders(first: 50, after: $cursor, query: $search, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      currencyCode
      displayFinancialStatus
      displayFulfillmentStatus
      discountCodes
      sourceName
      subtotalPriceSet { shopMoney { amount currencyCode } }
      totalDiscountsSet { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      totalTaxSet { shopMoney { amount } }
      totalPriceSet { shopMoney { amount } }
      lineItems(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          sku
          name
          quantity
          product { id }
          originalUnitPriceSet { shopMoney { amount } }
          totalDiscountSet { shopMoney { amount } }
        }
      }
      refunds {
        id
        createdAt
        note
        refundLineItems(first: 100) {
          nodes {
            id
            quantity
            restockType
            lineItem { id }
            subtotalSet { shopMoney { amount currencyCode } }
          }
        }
      }
    }
  }
}
"""

PRODUCTS_QUERY = """
query NorthstarProducts($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      productType
      status
      variants(first: 100) { nodes { sku price } }
    }
  }
}
"""


def gid_to_int(gid: str | None) -> str:
    match = _GID_NUM.search(gid or "")
    return match.group(1) if match else ""


class ShopifyGraphQLAdapter:
    """Read-only Shopify GraphQL Admin API client (orders, line items, refunds, products).

    Needs a custom-app Admin API access token with ``read_orders`` and
    ``read_products`` scopes (``read_all_orders`` for orders older than 60 days).
    It only sends queries — there are no mutations anywhere in this module.
    """

    name = "shopify:graphql"

    def __init__(self, store_domain: str, access_token: str, api_version: str, *,
                 client: Any | None = None, max_retries: int = 4, sleep: Callable[[float], None] = time.sleep):
        if not store_domain or not access_token:
            raise IngestionError("Live Shopify needs SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_ACCESS_TOKEN")
        self.url = f"https://{store_domain}/admin/api/{api_version}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
        self.max_retries = max_retries
        self.sleep = sleep
        if client is None:
            import httpx

            client = httpx.Client(timeout=30)
        self.client = client

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            response = self.client.post(self.url, json={"query": query, "variables": variables}, headers=self.headers)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                self.sleep(min(2 ** attempt, 20))
                continue
            if response.status_code in (401, 403):
                raise IngestionError(f"Shopify rejected the token (HTTP {response.status_code}). Check scopes.")
            response.raise_for_status()
            payload = response.json()
            errors = payload.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) and attempt < self.max_retries:
                self.sleep(min(2 ** attempt, 20))
                continue
            if errors:
                raise IngestionError(f"Shopify GraphQL error: {errors[0].get('message', errors)}")
            return payload["data"]
        raise IngestionError("Shopify request kept failing after retries (rate limited or unavailable)")

    def _paginate(self, query: str, root: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        cursor = None
        while True:
            data = self._post(query, {**variables, "cursor": cursor})[root]
            nodes.extend(data["nodes"])
            if not data["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = data["pageInfo"]["endCursor"]

    def fetch(self, start: str, end: str, issues: list[DataQualityIssue]) -> dict[str, RawTable]:
        # updated_at (not created_at) so refunds processed in the window on
        # older orders are included. Sales are later filtered by order date.
        search = f"updated_at:>='{start}T00:00:00Z'"
        orders = self._paginate(ORDERS_QUERY, "orders", {"search": search})
        products = self._paginate(PRODUCTS_QUERY, "products", {})
        tables = {t: RawTable(t, self.name, columns=REQUIRED[t] + ["order_name", "financial_status"])
                  for t in ("orders", "order_items", "refunds")}
        tables["products"] = RawTable("products", self.name, columns=REQUIRED["products"] + ["category", "list_price"])
        for p in products:
            for v in p["variants"]["nodes"]:
                if not v.get("sku"):
                    continue
                tables["products"].rows.append({"product_id": gid_to_int(p["id"]), "sku": v["sku"], "title": p["title"],
                                                "category": p.get("productType") or "", "list_price": v.get("price"),
                                                "status": (p.get("status") or "").lower()})
        for o in orders:
            def money(key: str, o: dict[str, Any] = o) -> str:
                return (o.get(key) or {}).get("shopMoney", {}).get("amount", "")

            order_id = gid_to_int(o["id"])
            tables["orders"].rows.append({
                "order_id": order_id, "order_name": o.get("name", ""), "created_at": o["createdAt"],
                "customer_id": "", "currency": o.get("currencyCode", ""),
                "financial_status": (o.get("displayFinancialStatus") or "").lower(),
                "fulfillment_status": (o.get("displayFulfillmentStatus") or "").lower(),
                "discount_codes": ",".join(o.get("discountCodes") or []),
                "subtotal_price": money("subtotalPriceSet"), "total_discounts": money("totalDiscountsSet"),
                "shipping_charged": money("totalShippingPriceSet"), "total_tax": money("totalTaxSet"),
                "total_price": money("totalPriceSet"), "source_name": o.get("sourceName") or "",
                "payment_gateway": "",
            })
            if o["lineItems"]["pageInfo"]["hasNextPage"]:
                issues.append(DataQualityIssue(self.name, "truncated_line_items", "error",
                                               "Order has more than 100 line items; extra lines not fetched.", order_id))
            for li in o["lineItems"]["nodes"]:
                tables["order_items"].rows.append({
                    "line_item_id": gid_to_int(li["id"]), "order_id": order_id,
                    "product_id": gid_to_int((li.get("product") or {}).get("id")), "sku": li.get("sku") or "",
                    "product_title": li.get("name", ""), "quantity": li["quantity"],
                    "unit_price": li["originalUnitPriceSet"]["shopMoney"]["amount"],
                    "total_discount": li["totalDiscountSet"]["shopMoney"]["amount"],
                })
            for refund in o.get("refunds") or []:
                for rli in refund["refundLineItems"]["nodes"]:
                    tables["refunds"].rows.append({
                        "refund_id": gid_to_int(rli["id"]), "order_id": order_id,
                        "line_item_id": gid_to_int((rli.get("lineItem") or {}).get("id")),
                        "processed_at": refund["createdAt"], "quantity": rli.get("quantity", ""),
                        "refund_amount": rli["subtotalSet"]["shopMoney"]["amount"],
                        "restocked": "true" if (rli.get("restockType") or "").upper() in {"RETURN", "CANCEL"} else "false",
                        "reason": refund.get("note") or "",
                        "currency": rli["subtotalSet"]["shopMoney"].get("currencyCode", o.get("currencyCode", "")),
                    })
        # Keep only orders created inside the window; refunds stay if processed inside it.
        in_window = {r["order_id"] for r in tables["orders"].rows if start <= r["created_at"][:10] <= end}
        tables["refunds"].rows = [r for r in tables["refunds"].rows if start <= r["processed_at"][:10] <= end]
        refunded_orders = {r["order_id"] for r in tables["refunds"].rows}
        keep = in_window | refunded_orders  # refunded older orders are kept so refunds can be validated
        tables["orders"].rows = [r for r in tables["orders"].rows if r["order_id"] in keep]
        tables["order_items"].rows = [r for r in tables["order_items"].rows if r["order_id"] in keep]
        return tables
