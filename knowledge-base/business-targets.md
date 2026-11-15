# Business Targets (FICTIONAL)

> These targets are **invented for teaching**. They are loaded from
> `data/google-sheets/business-targets.csv` into the `business_targets` table
> and returned by the `get_business_targets` tool. The CSV is authoritative; this
> page explains what each target means.

| Metric key | Target | Direction | Meaning |
|---|---|---|---|
| `contribution_margin` | 25% | at least | Contribution profit ÷ net product sales |
| `net_sales_growth_wow` | 3% | at least | Week-over-week net product sales growth |
| `ad_spend_pct_of_net_sales` | 22% | at most | Meta spend ÷ net product sales |
| `blended_mer` | 4.5 | at least | Net product sales ÷ Meta spend (Shopify-based efficiency) |
| `meta_platform_roas` | 2.5 | at least | Meta-reported purchase value ÷ spend (attribution-based; directional only) |
| `cogs_pct_of_net_sales` | 42% | at most | COGS ÷ net product sales |
| `fees_per_order` | $6.50 | at most | (Fulfillment + payment fees) ÷ orders |
| `refund_rate` | 4% | at most | Refunds ÷ gross product sales |
| `product_gross_margin` | 55% | at least | Per SKU: (net sales − COGS) ÷ net sales |

## How to use targets in analysis

- Compare actuals to targets **after** confirming the data is complete.
- Say "below target" or "above target" with both numbers, e.g.
  "contribution margin 15.5% vs. a 25% target".
- A missed target is a fact. The reason it was missed is a separate question
  that needs evidence.
- `meta_platform_roas` is attribution-based; never use it alone to judge
  whether advertising "worked". Pair it with `blended_mer`, which uses Shopify sales.
- Product margin targets exclude ads and fulfillment (not allocated to products).
