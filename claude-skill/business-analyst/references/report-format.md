# Report Format

```markdown
# <Weekly Business Review | Investigation>: <period>

Company · Current period <dates> vs previous <dates> · Timezone · Currency · Data: <sample/uploaded/connected>
> Contribution profit = net sales − COGS − fulfillment − payment fees − ad spend. Not net income.

## Executive summary
- 3–5 bullets. Lead with the answer. Absolute and % changes.

## Revenue overview
| Metric | Previous | Current | Change |   (gross, discounts, refunds, net, orders, AOV)

## Contribution profit and bridge
| Component | Previous | Current | Change | Profit impact |
Bridge check: impacts sum to <X> = profit change <X> (reconciles: yes/no)

## Advertising
Spend, spend % of sales, blended MER, campaign table (spend change, share of change),
Meta-reported ROAS (labelled attribution-based)

## Product profitability
SKU table: units, net sales, margin %, share of sales; missing costs flagged

## Refunds and fulfillment
Fee categories and per-order costs; refund rate; refund reasons (unverified)

## Findings
| # | Type (fact / calculation / hypothesis) | Finding | Source |

## Data-quality warnings and gaps

## Recommended next steps
1. Owner: action — why (evidence)
```

Rules: same numbers everywhere; money to 2 decimals in tables; percentages
to 1 decimal in prose; "points" for percentage-point changes; cite the file
and calculation behind each table; mark fictional data as fictional.

If you produce charts: use values computed by code from the data, one y-axis
per chart, and show a waterfall/bridge only when contribution profit is complete.
