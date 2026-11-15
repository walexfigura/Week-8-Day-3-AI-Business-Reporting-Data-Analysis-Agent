# Workflow: Anomaly detection

Use when asked "anything unusual?" or as part of the weekly review.

## What the rules are

`detect_anomalies` runs **simple, transparent threshold rules** — not machine
learning and not statistical tests. Defaults (in `analysis/anomalies.py`):

| Rule | Fires when |
|---|---|
| `data_completeness` | missing days, unequal or overlapping periods |
| `ad_spend_increase` | Meta spend up > 30% |
| `ad_spend_outpaced_sales` | spend growth exceeds net-sales growth by > 20 points |
| `contribution_margin_drop` | margin down > 5 points |
| `refund_rate_increase` | refund rate up > 1.5 points |
| `fees_per_order_increase` | fees per order up > 15% |
| `cogs_rate_increase` | COGS % of net sales up > 2 points |
| `missing_product_cost` | any sold SKU without a cost |
| `platform_roas_drop` | Meta-reported ROAS down > 20% |
| `platform_value_exceeds_shopify` | Meta-attributed value > Shopify net sales |
| `campaign_spend_increase` | a campaign's spend up > 50% and > $1,000 |
| `product_margin_drop` | a SKU's margin down > 5 points |

## Steps

1. Run `detect_anomalies` for the two periods.
2. Handle `data_completeness` flags first — they limit every other conclusion.
3. For each remaining flag, ask: is it material to profit or to a decision?
   Drill into material ones with the matching tool. Briefly list the rest.
4. A threshold firing is an observation, not a diagnosis. Explanations are hypotheses.
5. Say that the rules are thresholds and could miss subtler issues or flag noise.
