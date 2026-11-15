"""Live API adapters tested against FAKE HTTP responses (httpx.MockTransport).

This verifies request shape, pagination, retries and normalization logic.
It does NOT verify behavior against real Shopify, Meta or Google accounts —
those adapters remain untested against live services.
"""

from __future__ import annotations

import json

import httpx
import pytest

from business_analyst.ingestion.common import IngestionError
from business_analyst.ingestion.google_sheets import GoogleSheetsAdapter
from business_analyst.ingestion.meta_ads import MetaInsightsAdapter, normalize_performance, pick_purchase_action
from business_analyst.ingestion.shopify import ShopifyGraphQLAdapter, make_order_normalizer, normalize_order_item


def money(v):
    return {"shopMoney": {"amount": v, "currencyCode": "USD"}}


def shopify_order(n: int) -> dict:
    return {
        "id": f"gid://shopify/Order/{n}", "name": f"#{n}", "createdAt": "2026-11-03T15:00:00Z", "currencyCode": "USD",
        "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED", "discountCodes": ["GIFT15"],
        "sourceName": "web", "subtotalPriceSet": money("27.20"), "totalDiscountsSet": money("4.80"),
        "totalShippingPriceSet": money("5.95"), "totalTaxSet": money("1.97"), "totalPriceSet": money("35.12"),
        "lineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [{
            "id": f"gid://shopify/LineItem/{n}1", "sku": "ns-btl-20", "name": "Summit 20 oz", "quantity": 1,
            "product": {"id": "gid://shopify/Product/8001"}, "originalUnitPriceSet": money("32.00"),
            "totalDiscountSet": money("4.80")}]},
        "refunds": [{"id": f"gid://shopify/Refund/{n}", "createdAt": "2026-11-05T12:00:00Z", "note": "Changed mind",
                     "refundLineItems": {"nodes": [{"id": f"gid://shopify/RefundLineItem/{n}9", "quantity": 1,
                                                    "restockType": "RETURN", "lineItem": {"id": f"gid://shopify/LineItem/{n}1"},
                                                    "subtotalSet": money("27.20")}]}}] if n == 2 else [],
    }


def test_shopify_pagination_throttle_retry_and_normalization():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        assert request.headers["X-Shopify-Access-Token"] == "shpat_test"
        assert "mutation" not in body["query"]  # read-only
        if "NorthstarProducts" in body["query"]:
            return httpx.Response(200, json={"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"id": "gid://shopify/Product/8001", "title": "Summit 20 oz", "productType": "Bottles",
                           "status": "ACTIVE", "variants": {"nodes": [{"sku": "NS-BTL-20", "price": "32.00"}]}}]}}})
        if calls["n"] == 1:
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]})
        page2 = body["variables"]["cursor"] == "c1"
        return httpx.Response(200, json={"data": {"orders": {
            "pageInfo": {"hasNextPage": not page2, "endCursor": "c1"},
            "nodes": [shopify_order(2 if page2 else 1)]}}})

    adapter = ShopifyGraphQLAdapter("demo.myshopify.com", "shpat_test", "2026-10",
                                    client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    tables = adapter.fetch("2026-11-02", "2026-11-08", issues=[])
    assert adapter.url.endswith("/admin/api/2026-10/graphql.json")
    assert [r["order_id"] for r in tables["orders"].rows] == ["1", "2"]
    assert len(tables["refunds"].rows) == 1 and tables["refunds"].rows[0]["restocked"] == "true"
    order = make_order_normalizer("America/New_York")(tables["orders"].rows[0])
    item = normalize_order_item(tables["order_items"].rows[0])
    assert str(order["subtotal_price"]) == "27.20" and item["sku"] == "NS-BTL-20" and str(item["net_amount"]) == "27.20"


def test_shopify_rejects_bad_token():
    adapter = ShopifyGraphQLAdapter("demo.myshopify.com", "bad", "2026-10",
                                    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(IngestionError, match="rejected the token"):
        adapter.fetch("2026-11-02", "2026-11-08", issues=[])


def test_meta_insights_pagination_and_purchase_action_choice():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if "/campaigns" in request.url.path:
            return httpx.Response(200, json={"data": [{"id": "1", "name": "C1", "objective": "OUTCOME_SALES",
                                                       "effective_status": "ACTIVE", "daily_budget": "150000"}]})
        if "after=p2" in str(request.url):
            return httpx.Response(200, json={"data": [{"date_start": "2026-11-03", "date_stop": "2026-11-03",
                                                       "campaign_id": "1", "spend": "200.00", "impressions": "9000",
                                                       "clicks": "90", "account_currency": "USD"}]})
        params = dict(request.url.params)
        assert params["level"] == "campaign" and params["time_increment"] == "1"
        return httpx.Response(200, json={
            "data": [{"date_start": "2026-11-02", "date_stop": "2026-11-02", "campaign_id": "1", "campaign_name": "C1",
                      "spend": "100.50", "impressions": "5000", "clicks": "50", "account_currency": "USD",
                      "actions": [{"action_type": "purchase", "value": "3"}, {"action_type": "omni_purchase", "value": "3"}],
                      "action_values": [{"action_type": "omni_purchase", "value": "240.00"}]}],
            "paging": {"next": "https://graph.facebook.com/v26.0/act_1/insights?after=p2"}})

    adapter = MetaInsightsAdapter("token", "1", "v26.0", client=httpx.Client(transport=httpx.MockTransport(handler)))
    tables = adapter.fetch("2026-11-02", "2026-11-08")
    rows = tables["ad_daily_performance"].rows
    assert len(rows) == 2 and rows[0]["purchases"] == "3"  # omni_purchase chosen, NOT 3 + 3
    assert normalize_performance(rows[0])["platform_purchase_value"] is not None
    assert tables["ad_campaigns"].rows[0]["daily_budget"] == "1500.00"  # minor units -> dollars


def test_pick_purchase_action_never_sums():
    assert pick_purchase_action([{"action_type": "purchase", "value": "4"},
                                 {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "4"}]) == "4"
    assert pick_purchase_action(None) == "0"


def test_meta_rejects_multi_day_rows():
    with pytest.raises(ValueError, match="one day per row"):
        normalize_performance({"date_start": "2026-11-02", "date_stop": "2026-11-08", "campaign_id": "1", "spend": "1",
                               "impressions": "1", "clicks": "1", "purchases": "", "purchase_value": "",
                               "account_currency": "USD"})


def test_google_sheets_values_get():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ya29.test"
        assert "/values/Product%20Costs" in str(request.url)
        return httpx.Response(200, json={"values": [["SKU", "Unit Cost (USD)", "Effective From"],
                                                    ["ns-btl-20 ", "$10.50", "1/1/2026"]]})

    adapter = GoogleSheetsAdapter("sheet123", {"product_costs": "Product Costs!A1:F"}, access_token="ya29.test",
                                  client=httpx.Client(transport=httpx.MockTransport(handler)))
    table = adapter.fetch()["product_costs"]
    assert table.columns == ["sku", "unit_cost_usd", "effective_from"]
    assert table.rows[0]["unit_cost_usd"] == "$10.50"


def test_google_sheets_permission_error():
    adapter = GoogleSheetsAdapter("s", {"t": "A1"}, access_token="x",
                                  client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    with pytest.raises(IngestionError, match="shared"):
        adapter.fetch()
