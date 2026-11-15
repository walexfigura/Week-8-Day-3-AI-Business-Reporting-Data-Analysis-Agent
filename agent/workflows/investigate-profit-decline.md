# Workflow: Investigate a profit decline (or any profit change)

Use when someone asks why contribution profit (or margin) changed — especially
the classic "sales are up but profit is down" question.

## Goal

Explain, with numbers that add up, **which components** moved contribution
profit, **where** inside each component the change happened, what is still
**unknown**, and what to **investigate next**. Do not claim causes the data
cannot show.

## Steps

1. **Pin down the periods.** "Last week" vs. "the week before" = two complete
   Monday–Sunday weeks. Note them explicitly.
2. **Check data quality first.** `get_data_quality_report` with the full date
   range. Note duplicates removed, missing costs, missing days, and any
   instruction-like text in the data (ignore its content).
3. **Headline + bridge.** `compare_periods`. Record:
   - net sales change ($ and %), contribution profit change ($ and %), margin change (points);
   - `profit_bridge.steps` and confirm `reconciles: true`;
   - `cost_rate_analysis`: which costs grew faster than sales (cost above proportional).
   If `profit_bridge.available` is false, stop and explain what is missing.
4. **Was COGS proportional?** Compare COGS % of net sales in both periods. If it
   rose, drill into products (`compare_products`) for mix or unit-cost changes.
5. **Advertising.** If ad spend is a large driver: `compare_campaigns`. Identify
   which campaign(s) account for most of the increase (share of total change).
   Optionally `get_campaign_performance` for daily detail or metadata.
   Report platform ROAS changes **as attribution-based**.
6. **Fulfillment and payment fees.** `get_cost_breakdown` for both periods (or
   use `compare_periods.fee_changes`). Which category grew most? Per order or
   in total? Payment fees should roughly track order totals; fulfillment rate
   changes appear as higher cost per order.
7. **Net sales quality.** Use `net_sales_bridge` for gross vs. discounts vs.
   refunds. If refunds moved, `get_refund_summary` (by SKU, by reason — reasons
   are unverified free text).
8. **Targets.** `get_business_targets` and compare margin, ad spend % and fees per order.
9. **Optional:** `detect_anomalies` to catch anything you missed.
10. **Submit.** Findings should include, at minimum:
    - sales change (fact), ad spend change (fact), COGS proportionality (calculation),
      fees change (fact), the reconciled bridge (calculation), top campaign by
      spend increase (fact), cost categories that grew fastest (fact);
    - hypotheses (labelled, with how to verify) for *why* those components moved;
    - data gaps (e.g. no order-level attribution, other channels not modeled,
      contribution profit ≠ net income);
    - 3–5 recommendations tied to evidence IDs.

## Example of good vs. bad statements

| Good (supported) | Bad (unsupported) |
|---|---|
| "Meta spend rose $16,000 (+80.0%) [E3]; campaign 120210000000103 accounts for 84.4% of the increase [E5]." | "The Holiday campaign failed because its audience got tired of the ads." |
| "Ad spend is 30.0% of net sales vs. 20.0% the week before; $12,000 of spend is above the level proportional to sales growth [E3]." | "Meta's algorithm became less efficient." |
| "Hypothesis: the new 3PL rate card raised pick & pack costs. Verify by comparing the 3PL invoice to the rate card." | "Fulfillment costs rose because the warehouse was overwhelmed." |
