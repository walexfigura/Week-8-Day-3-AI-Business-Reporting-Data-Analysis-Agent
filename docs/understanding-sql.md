# Understanding the SQL in this project

You can run every query below yourself, read-only, with:

```bash
python run.py sql "SELECT COUNT(*) FROM orders"
```

The `sql` command uses the same guardrails as the AI's SQL tool: one SELECT
per call, known tables only, 200-row limit, 5-second timeout. Anything that
writes is rejected.

## 1. SELECT and WHERE — read rows, filter them

```sql
SELECT order_id, order_date, subtotal_price
FROM orders
WHERE order_date = '2026-11-08'
ORDER BY subtotal_price DESC
LIMIT 5
```

## 2. GROUP BY and SUM — totals per group

```sql
SELECT order_date, COUNT(*) AS orders, SUM(subtotal_price) AS sales_after_discounts
FROM orders
WHERE order_date BETWEEN '2026-11-02' AND '2026-11-08'
GROUP BY order_date
ORDER BY order_date
```

## 3. JOIN — combine tables on a key

Line items don't have a date; their order does. Joining items to orders is
**many-to-one** (each item has exactly one order), so no row is duplicated:

```sql
SELECT oi.sku, SUM(oi.quantity) AS units
FROM order_items AS oi
JOIN orders AS o ON o.order_id = oi.order_id
WHERE o.order_date BETWEEN '2026-11-02' AND '2026-11-08'
GROUP BY oi.sku
ORDER BY units DESC
```

## 4. The double-counting trap (the most important lesson)

Each **order** row carries `subtotal_price`. If you join orders to their line
items, an order with three items appears three times, and so does its money:

```sql
-- WRONG: inflated
SELECT SUM(o.subtotal_price)
FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.order_id
```

```sql
-- RIGHT: sum at the order grain
SELECT SUM(subtotal_price) FROM orders
```

Joining orders to daily ad rows by date is even worse: every order is repeated
once per campaign that ran that day. Try both and compare:

```sql
SELECT SUM(o.subtotal_price) AS inflated
FROM orders AS o JOIN ad_daily_performance AS a ON a.perf_date = o.order_date
```

**Rule:** aggregate each source to the same grain *first* (one row per day),
*then* join. That is exactly what `v_daily_contribution` does.

## 5. Common table expressions (WITH) — name intermediate steps

```sql
WITH sales AS (
    SELECT day, net_sales FROM v_daily_contribution
), ads AS (
    SELECT day, ad_spend FROM v_daily_ads
)
SELECT s.day, s.net_sales, a.ad_spend, ROUND(s.net_sales / a.ad_spend, 2) AS mer
FROM sales AS s JOIN ads AS a USING (day)
ORDER BY s.day
```

## 6. Date-based aggregation — weeks

```sql
SELECT date_trunc('week', day) AS week_start,     -- weeks start on Monday
       SUM(net_sales) AS net_sales,
       SUM(ad_spend) AS ad_spend,
       SUM(net_sales) - SUM(cogs_known) - SUM(fees_total) - SUM(ad_spend) AS contribution_profit
FROM v_daily_contribution
GROUP BY 1 ORDER BY 1
```

Expected: 100,000 / 20,000 / 30,000 for the week of Oct 26 and
120,000 / 36,000 / 18,600 for the week of Nov 2.

## 7. Effective-dated costs — a range join

Costs change over time. Each line uses the cost effective on its order date:

```sql
SELECT sku, order_date, unit_cost
FROM v_line_costs
WHERE sku = 'NS-BTL-32' AND order_date IN ('2026-11-01', '2026-11-02')
GROUP BY ALL
```

You'll see $12.75 on Nov 1 and $13.50 on Nov 2.

## 8. Missing is not zero — LEFT JOIN and NULL

`v_line_costs` uses a LEFT JOIN so lines **without** a cost are kept with a
NULL cost instead of disappearing. Try it on the missing-COGS scenario:

```bash
python run.py sql --scenario missing_cogs \
  "SELECT sku, SUM(quantity) AS units FROM v_line_costs WHERE unit_cost IS NULL GROUP BY sku"
```

`SUM()` ignores NULLs, so a naive COGS total would silently treat the missing
cost as zero. The tools count missing units explicitly and refuse to report a
complete COGS.

## Tables and views at a glance

| Name | Grain |
|---|---|
| `orders` | one row per order |
| `order_items` | one row per order line |
| `refunds` | one row per refund line (dated by refund date) |
| `products` | one row per SKU |
| `product_costs` | SKU × effective-date range |
| `fulfillment_costs` | day × cost category |
| `ad_campaigns` | one row per campaign |
| `ad_daily_performance` | campaign × day |
| `v_daily_sales`, `v_daily_refunds`, `v_daily_cogs`, `v_daily_fees`, `v_daily_ads` | one row per day |
| `v_daily_contribution` | one row per day, all profit components |
| `v_product_daily`, `v_campaign_daily` | SKU × day, campaign × day |

`python run.py tool get_available_tables` prints columns and row counts.
