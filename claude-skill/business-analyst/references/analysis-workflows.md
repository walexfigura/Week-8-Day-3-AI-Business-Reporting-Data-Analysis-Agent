# Analysis Workflows

All workflows start with validation (`data-validation.md`) and end with the
report format (`report-format.md`). Use code for every calculation.

## 1. Profit investigation ("sales up, profit down")

1. Periods: last complete week vs. the week before (equal lengths).
2. Validate coverage, duplicates, missing costs.
3. Run `scripts/kpi_calculator.py` for totals, % changes, the bridge and cost rates.
4. Confirm the bridge reconciles exactly. If contribution profit is unavailable,
   stop and explain what is missing.
5. For each large negative bridge step, drill down:
   - **Meta spend** → `campaign-daily.csv`: spend by campaign per period, each
     campaign's share of the total increase, CPM, Meta-reported ROAS (label as
     attribution-based).
   - **COGS** → is COGS % of net sales unchanged? If not, use `product-weekly.csv`
     (mix) and `product-costs.csv` (unit-cost changes with effective dates).
   - **Fulfillment/payment** → per-category totals and per-order costs from
     `daily-summary.csv` (pick_pack, packaging, shipping_labels, payment_processing ÷ orders).
   - **Refunds** → `refunds-weekly.csv` by SKU.
6. Compare with `business-targets.csv`.
7. Write findings (facts, calculations, hypotheses with verification steps),
   data gaps and 3–5 recommendations.

### Expected result on the sample data

| Metric | Previous | Current | Change |
|---|---|---|---|
| Net product sales | $100,000 | $120,000 | +20.0% |
| COGS | $40,000 | $48,000 | +20.0% (proportional: 40% of sales both weeks) |
| Fulfillment + payment fees | $10,000 | $17,400 | +74.0% |
| Meta ad spend | $20,000 | $36,000 | +80.0% |
| Contribution profit | $30,000 | $18,600 | −38.0% |

Bridge: +$20,000 sales − $8,000 COGS − $6,617.40 fulfillment − $782.60
payment − $16,000 Meta = −$11,400. Campaign `120210000000103` (Holiday Gift
Push) rose from $1,500 to $15,000: $13,500, 84.4% of the spend increase.
If costs had kept their previous share of sales, profit would be $36,000;
$12,000 of the gap is Meta spend above proportional and $5,400 is fees.

## 2. Weekly business review

Headline KPIs → bridge → anomaly checks → campaigns → products → refunds and
fulfillment → targets → data-quality warnings → next steps.

## 3. Campaign analysis

- Spend is a cost; Meta purchases and purchase value are **attributed** and
  overlap with store orders. Never add them to sales.
- Rank campaigns by spend change and share of the total change.
- Report CPM, CTR, CPC and Meta-reported ROAS per campaign, plus blended MER
  (store net sales ÷ total ad spend).
- No order-level attribution exists in the sample data: do not say a campaign
  sold specific products. `product_focus` = what it promotes.
- Incrementality needs an experiment (holdout or geo test).

## 4. Product profitability

Product net sales = line sales − discounts − refunds (by SKU); product margin
= product net sales − COGS. Ads and fulfillment are not allocated to products.
Explain COGS % changes by mix, unit-cost changes or discounting. A SKU with no
cost → margin unavailable.

## 5. Anomaly checks (simple thresholds)

| Rule | Threshold |
|---|---|
| Meta spend increase | > 30% |
| Spend growth minus sales growth | > 20 points |
| Contribution margin drop | > 5 points |
| Refund rate increase | > 1.5 points |
| Fees per order increase | > 15% |
| COGS % of sales increase | > 2 points |
| Any sold SKU without a cost | always |
| Campaign spend increase | > 50% and > $1,000 |
| Meta-reported value > store net sales | always |

Thresholds are transparent rules, not machine learning. A flag is an
observation, not a diagnosis.
