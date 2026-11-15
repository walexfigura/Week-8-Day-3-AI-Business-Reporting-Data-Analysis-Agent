# Data Source Guide

Three systems feed the analytical database. They were never designed to fit
together, so ingestion turns them into consistent tables first.

| Source | Mock file(s) | Real API (read-only adapter) | DuckDB table(s) | Grain |
|---|---|---|---|---|
| Shopify | `data/shopify/products.csv` | GraphQL Admin `products` | `products` | one row per SKU |
| Shopify | `data/shopify/orders.csv` | GraphQL Admin `orders` | `orders` | one row per order |
| Shopify | `data/shopify/order-items.csv` | `orders.lineItems` | `order_items` | one row per order line |
| Shopify | `data/shopify/refunds.csv` | `orders.refunds.refundLineItems` | `refunds` | one row per refund line |
| Meta Ads | `data/meta/campaign-performance.csv` | Marketing API Insights (`level=campaign`, `time_increment=1`) | `ad_daily_performance` | campaign × day |
| Meta Ads | `data/meta/campaign-metadata.csv` | `/{ad_account}/campaigns` | `ad_campaigns` | one row per campaign |
| Google Sheets | `data/google-sheets/product-costs.csv` | Sheets API `values.get` | `product_costs` | SKU × effective-date range |
| Google Sheets | `data/google-sheets/fulfillment-costs.csv` | Sheets API `values.get` | `fulfillment_costs` | day × cost category |
| Google Sheets | `data/google-sheets/business-targets.csv` | Sheets API `values.get` | `business_targets` | one row per target |

## Shopify

- `created_at` / `processed_at` are **UTC** ISO timestamps. Ingestion converts
  them to the reporting-timezone date (`order_date`, `refund_date`).
- `subtotal_price` = products after discounts (no shipping, no tax).
- `total_discounts` (order) = sum of line `total_discount`. Same money, two grains.
- `total_price` = subtotal + shipping + tax. **Not** product sales.
- `financial_status` (`paid`, `partially_refunded`, `refunded`) is informational;
  refunds are measured from the refunds table.
- Refund `reason` is free text typed by staff or customers: untrusted.

## Meta Ads

- `spend` is in the ad account currency (USD) and is a real cost.
- `purchases` and `purchase_value` are **Meta-attributed** results (stored as
  `platform_purchases`, `platform_purchase_value`). They are not Shopify sales.
- The data contains no order-level attribution, so the analyst must not link
  specific orders or products to specific campaigns. `product_focus` in the
  metadata describes what a campaign *promotes*, not what it *sold*.
- Insights dates use the ad account timezone (assumed America/New_York here).
- Campaign names and notes are free text: untrusted.

## Google Sheets (finance team)

- Headers are human-friendly ("Unit Cost (USD)") and are normalized to
  snake_case at ingestion (`unit_cost_usd`).
- Money is text like `$1,234.50`; dates are US format `11/2/2026`.
- SKUs are typed by hand. Ingestion upper-cases and trims them and records an
  `sku_normalized` info issue (one exists in the base data: `ns-brs-cln `).
- `product-costs.csv` uses effective dates; the cost used for a line is the
  one effective on the **order date**.
- `fulfillment-costs.csv` holds daily invoice totals by category, assigned to
  the order date, with an "Orders Covered" column that must match Shopify's
  daily order count (a validation query checks it).

## Normalization summary

| Problem | Example | Rule |
|---|---|---|
| Different date formats | `2026-11-02T01:15:00Z`, `11/2/2026` | Parse explicitly; UTC → reporting date |
| Money as text | `$1,234.50` | Strip symbols, parse as DECIMAL |
| Inconsistent SKUs | `ns-brs-cln ` | Upper-case, remove whitespace, log it |
| Currency codes | `usd` | Upper-case; reject non-USD |
| Duplicates | overlapping export pages | Drop exact duplicates (warning); conflicting duplicates = error |
| Malformed rows | negative quantity, blank price | Reject the row and record an error — never guess |
| Granularity | weekly rows mixed into daily Meta data | Reject non-daily rows |
