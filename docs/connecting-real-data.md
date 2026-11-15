# Connecting Real Data

The default demo uses fictional CSVs. This guide explains how to point the
same pipeline at real Shopify, Meta Ads and Google Sheets data.

> **Status, honestly:** the live adapters in `src/business_analyst/ingestion/`
> are implemented (read-only, paginated, with retries and normalization) and
> unit-tested against **fake** HTTP responses (`tests/test_live_adapters.py`).
> They have **not** been run against real accounts. Expect to adjust field
> names and permissions for your store, and validate the numbers against the
> platforms' own reports before trusting them.

## How it plugs in

```
DATA_SOURCE=live  →  ShopifyGraphQLAdapter + MetaInsightsAdapter + GoogleSheetsAdapter
                  →  same RawTable shapes as the mock CSVs
                  →  same normalization, dedupe, DuckDB schema, views, tools
```

Build a live database (date window from `LIVE_START_DATE` / `LIVE_END_DATE`):

```bash
python run.py init-db --source live
python run.py doctor        # still runs the offline checks
```

Scenarios only apply to mock data. Live databases are never rebuilt
automatically (that would call APIs on every run); re-run `init-db --source live` to refresh.

## Shopify (GraphQL Admin API)

1. In Shopify admin: **Settings → Apps and sales channels → Develop apps** →
   create a custom app → Admin API scopes: `read_orders`, `read_products`
   (add `read_all_orders` for orders older than 60 days).
2. Install the app and copy the Admin API access token.
3. `.env`:
   ```
   SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
   SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_...
   SHOPIFY_API_VERSION=2026-10
   ```
- **What it fetches:** orders (with line items, discounts, refunds) updated
  since the start date, 50 per page via cursor pagination, and product variants.
- **Rate limits:** GraphQL uses a cost-based leaky bucket. The adapter retries
  `THROTTLED` errors and HTTP 429/5xx with exponential backoff. Large stores
  should use bulk operations instead of paging; that is not implemented here.
- **Data caveats:** orders are fetched by `updated_at` so refunds processed in
  the window on older orders are included. Multi-currency stores need FX
  handling (non-USD rows are rejected). Line-item discount fields differ
  between API versions: check `totalDiscountSet` vs. `discountAllocations` for yours.
- **Privacy:** the query does not request customer names, emails or addresses.

## Meta Marketing API (Insights)

1. Create a Meta app, add the Marketing API product, and generate a system-user
   token with `ads_read` for the ad account.
2. `.env`:
   ```
   META_ACCESS_TOKEN=...
   META_AD_ACCOUNT_ID=act_1234567890
   META_API_VERSION=v26.0
   ```
- **What it fetches:** `/{act}/insights?level=campaign&time_increment=1` with
  spend, impressions, clicks, `actions` and `action_values`, following
  `paging.next`; plus `/{act}/campaigns` metadata. Budgets arrive in cents
  and are converted.
- **Purchases:** Meta returns several purchase action types for the same
  purchases. The adapter picks the first of `omni_purchase`, `purchase`,
  `offsite_conversion.fb_pixel_purchase` — it never sums them.
- **Rate limits:** error codes 4/17/32/613/80004 are retried with backoff.
  For long date ranges or many campaigns, use asynchronous report runs.
- **Reporting caveats:** results are attributed by Meta's attribution setting
  (e.g. 7-day click / 1-day view), can change for days afterwards as
  conversions are modeled, and use the **ad account timezone**. If your
  account timezone is not the reporting timezone, daily joins will be off.
  Platform purchases are never added to Shopify sales.
- Graph API versions are deprecated on a schedule: check the changelog and
  update `META_API_VERSION`.

## Google Sheets API

1. Create a Google Cloud project, enable the **Google Sheets API**, create a
   service account and download its JSON key. Keep it outside the repository
   (`service-account*.json` is git-ignored anyway).
2. Share the spreadsheet with the service account's email as **Viewer**.
3. `pip install -e ".[live]"` (adds `google-auth`), then `.env`:
   ```
   GOOGLE_SHEETS_SPREADSHEET_ID=1AbC...
   GOOGLE_SERVICE_ACCOUNT_FILE=/secure/path/key.json
   GOOGLE_SHEETS_PRODUCT_COSTS_RANGE=Product Costs!A1:F
   GOOGLE_SHEETS_FULFILLMENT_RANGE=Fulfillment Costs!A1:F
   GOOGLE_SHEETS_TARGETS_RANGE=Targets!A1:F
   ```
   (Alternatively set `GOOGLE_SHEETS_ACCESS_TOKEN` to a short-lived OAuth token
   with the `spreadsheets.readonly` scope.)
- **What it fetches:** `spreadsheets.values.get` for each range with
  `FORMATTED_VALUE`, so values look like the CSV exports (`$1,234.50`, `11/2/2026`).
- The tabs must use the same headers as the mock CSVs (see `data/google-sheets/`).
- People edit sheets: expect typos, merged cells and changed headers. A missing
  required column stops the build with a clear error instead of loading bad data.

## Permissions checklist

| System | Minimum permission | Never grant |
|---|---|---|
| Shopify | `read_orders`, `read_products` | any `write_*` scope |
| Meta | `ads_read` | `ads_management` |
| Google Sheets | Viewer on one spreadsheet, `spreadsheets.readonly` | Editor, Drive-wide scopes |
| OpenAI | project API key with a spending limit | organization admin keys |

## Normalization reminders

- Convert every timestamp to the reporting timezone before grouping by day.
- Keep one grain per table and aggregate before joining.
- Record every row you drop or change (`data_quality_issues`).
- Re-run `python run.py eval` after changes: the deterministic suite still uses
  mock data, so it checks the logic without touching your accounts.
