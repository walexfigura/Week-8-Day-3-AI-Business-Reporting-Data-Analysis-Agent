<!-- Synced from knowledge-base/metric-definitions.md by scripts/build_skill.py. Edit the original, not this copy. Mentions of Python modules and tools refer to the companion repository; inside the Skill, apply the same formulas with code execution or the bundled scripts/kpi_calculator.py. -->

# Metric Definitions — Northstar Supply (fictional)

This file is the single source of truth for every metric the AI Business
Analyst reports. The Python code in `src/business_analyst/analysis/metrics.py`
implements exactly these formulas, and the `explain_metric_definition` tool
returns the sections below to the agent.

**General rules for every metric**

- Reporting timezone: **America/New_York**. Reporting currency: **USD**.
- Periods are inclusive calendar date ranges. Standard week: Monday–Sunday.
- **Missing is not zero.** If a required input is missing, the metric is
  reported as unavailable (or as an explicitly labelled bound), never as if
  the missing value were 0.
- Each money total is computed once, at its own grain, and only then combined
  with other totals (see `docs/understanding-sql.md` on double counting).

---

## Sales metrics

### Gross product sales

- **Definition:** The list value of products ordered in the period, before discounts and refunds.
- **Formula:** `SUM(order_items.quantity × order_items.unit_price)` for orders whose `order_date` is in the period.
- **Required data:** Shopify order line items and order dates.
- **Missing values:** A line with a missing quantity or price is rejected at ingestion and reported as a data-quality error; it is not counted as $0.
- **Caveats:** Excludes shipping charged to customers and sales tax. Do not use `orders.total_price` (it includes shipping and tax).

### Discounts

- **Definition:** Price reductions applied to the products in the period's orders (discount codes, automatic discounts, manual price matches).
- **Formula:** `SUM(order_items.discount_amount)` for orders in the period.
- **Required data:** Line-level discount allocations.
- **Missing values:** Blank discount on a line is rejected at ingestion (a real export always has a value, even `0.00`).
- **Caveats:** Shopify also reports `orders.total_discounts` and an order `subtotal_price` that is **already net of discounts**. These describe the same discounts at a different grain. Subtract discounts **once** — never subtract both the line and the order totals, and never subtract discounts from `subtotal_price`.

### Refunds

- **Definition:** Money returned to customers for products, assigned to the period in which the refund was **processed**.
- **Formula:** `SUM(refunds.refund_amount)` where `refund_date` is in the period.
- **Required data:** Shopify refund records with processed timestamps.
- **Missing values:** A day with no refund rows (in a period where Shopify data is complete) means no refunds that day. A refund record with a missing amount is rejected and reported.
- **Caveats:** A refund in this week may relate to an order from an earlier week. That is intentional (cash-basis assignment) and must be disclosed when refunds change materially. Refund reasons are free text and unverified.

### Net product sales

- **Definition:** Product revenue after discounts and refunds. This is the company's primary sales metric.
- **Formula:** `Gross product sales − Discounts − Refunds`
- **Required data:** Line items, discounts, refunds.
- **Missing values:** Unavailable if Shopify data is missing for any day of the period.
- **Caveats:** Excludes shipping and taxes. **Not the same as Meta "purchase conversion value"** — see below. Never add Meta-attributed value to net product sales.

### Order count

- **Definition:** Number of distinct Shopify orders placed in the period.
- **Formula:** `COUNT(DISTINCT orders.order_id)` by `order_date`.
- **Required data:** Shopify orders.
- **Missing values:** Unavailable if any day lacks Shopify data.
- **Caveats:** Count orders from the `orders` table (or a per-order aggregate). Counting rows after joining line items counts lines, not orders.

### Average order value

- **Definition:** Average product value per order after discounts, before refunds.
- **Formula:** `(Gross product sales − Discounts) ÷ Order count`
- **Required data:** Line items, discounts, orders.
- **Missing values:** Undefined when there are no orders.
- **Caveats:** Refunds are excluded because they are assigned by refund date and may belong to other periods' orders.

### Units sold

- **Definition:** Total product units ordered in the period.
- **Formula:** `SUM(order_items.quantity)` for orders in the period.
- **Required data:** Line items.
- **Missing values:** Rows with missing quantity are rejected and reported.
- **Caveats:** Refunded units are reported separately; units sold are not reduced by refunds.

---

## Cost metrics

### Cost of goods sold

- **Definition:** Landed product cost of units ordered in the period (COGS).
- **Formula:** `SUM(quantity × unit_cost)` where `unit_cost` is the Google Sheet cost **effective on the order date** for that SKU.
- **Required data:** Line items, order dates, product cost sheet with effective dates.
- **Missing values:** If any sold SKU has no effective cost, COGS is **incomplete**: report the known portion, list the missing SKUs and units, and do **not** report a complete COGS or contribution profit. Never assume a missing cost is $0.
- **Caveats:** Refunded units are not reversed out of COGS in this exercise (refunded items are assumed not to be resold). Overlapping cost date ranges for one SKU would double-count COGS; a validation query checks for this.

### Fulfillment costs

- **Definition:** Third-party logistics and shipping costs: pick & pack, packaging, and shipping labels.
- **Formula:** `SUM(fulfillment_costs.amount)` for categories `pick_pack`, `packaging`, `shipping_labels`, by cost date.
- **Required data:** Daily 3PL and carrier invoice lines (Google Sheet).
- **Missing values:** A day with no cost rows is missing data, not zero. The period is incomplete.
- **Caveats:** Costs are assigned to the **order date** in the sheet. Shipping charged to customers is not netted against these costs (shipping revenue is outside net product sales).

### Payment processing fees

- **Definition:** Fees charged by the payment processor on customer payments.
- **Formula:** `SUM(fulfillment_costs.amount)` for category `payment_processing`. The processor charges 2.9% + $0.30 of each order's charged total (products after discounts + shipping + tax).
- **Required data:** Payment processor payout report (Google Sheet).
- **Missing values:** Missing days make the period incomplete.
- **Caveats:** Fees are based on the total charged, including shipping and tax, so they grow with order count and order totals, not only with net product sales. Fees are not refunded when an order is refunded.

### Advertising spend

- **Definition:** Money spent on Meta (Facebook/Instagram) advertising in the period.
- **Formula:** `SUM(ad_daily_performance.spend)` by `perf_date`.
- **Required data:** Meta Insights daily campaign rows.
- **Missing values:** A day with no Meta rows is missing data (Meta exports can lag 1–3 days). Report ad spend as incomplete.
- **Caveats:** Only Meta is modeled. Other marketing channels are not included. Meta dates are in the ad account's timezone; this dataset assumes it matches the reporting timezone.

---

## Efficiency and profit metrics

### ROAS

- **Definition:** Return on ad spend **as reported by Meta**: Meta-attributed purchase value per dollar of spend.
- **Formula:** `SUM(platform_purchase_value) ÷ SUM(spend)`
- **Required data:** Meta Insights spend and purchase value.
- **Missing values:** Undefined when spend is zero or purchase value is missing.
- **Caveats:** Meta's purchase value is **attributed**, not measured revenue. Meta counts a purchase if someone saw or clicked an ad within its attribution window, so the same Shopify order can also be credited to email, search or another channel. Meta's value can be larger or smaller than actual Shopify sales and cannot prove incrementality (whether the sale would have happened anyway). Use it to compare campaigns and trends, not as revenue. For a Shopify-based efficiency view use **blended MER** = Net product sales ÷ Meta ad spend.

### Contribution profit

- **Definition:** What is left from net product sales after the variable costs modeled in this project.
- **Formula:** `Net product sales − COGS − Fulfillment costs − Payment processing fees − Meta advertising spend`
- **Required data:** All of the inputs above for every day of the period.
- **Missing values:** If any component is incomplete, contribution profit is **unavailable**. When only COGS is incomplete, the tools return an **upper bound** (true profit is lower, because missing costs are ≥ 0), clearly labelled as a bound.
- **Caveats:** An illustrative metric, **not accounting net income** and not the company's full profitability. It excludes other marketing channels, salaries, software, rent, other fixed operating expenses, taxes and overhead.

### Contribution margin

- **Definition:** Contribution profit as a percentage of net product sales.
- **Formula:** `Contribution profit ÷ Net product sales × 100`
- **Required data:** Contribution profit and net product sales.
- **Missing values:** Unavailable whenever contribution profit is unavailable, or when net sales are zero.
- **Caveats:** A margin change can come from price/discount changes, cost-rate changes, or mix; use the cost-rate analysis to see which.

### Period-over-period percentage change

- **Definition:** Relative change of a metric from a previous period to a current period.
- **Formula:** `(Current − Previous) ÷ |Previous| × 100`
- **Required data:** The metric for both periods, computed with identical definitions.
- **Missing values:** Undefined when the previous value is zero or missing (report the absolute change instead).
- **Caveats:** Compare periods of **equal length** (and ideally the same weekdays). If lengths differ, disclose it and compare daily averages. Also report the absolute change: −38% of a small number may matter less than −10% of a large one.

---

## Shopify sales vs. Meta attributed conversion value

| | Shopify net product sales | Meta purchase conversion value |
|---|---|---|
| What it measures | Money actually recorded by the store | Value Meta's attribution model credits to ads |
| Source of truth for revenue | **Yes** | No |
| Overlaps with other channels | No (each order once) | Yes (same order may be credited to several channels) |
| Includes refunds | Yes (subtracted) | Usually not |
| Can be added together | **No — never add them.** Meta value is a (partial, model-based) explanation of some of the same Shopify sales, not a separate revenue stream. |

Attribution limitations to state whenever ROAS or Meta purchases appear in an
answer: attribution windows, view-through credit, cross-device modeling,
privacy-related modeled conversions and delayed reporting all make platform
numbers approximations. Incrementality can only be estimated with
experiments (e.g. holdout or geo tests), not read from platform reports.
