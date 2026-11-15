# Workflow: Weekly business review

Use for "prepare the weekly review", "how did last week go?" or a scheduled report.

## Steps

1. Periods: last complete week vs. the week before.
2. `get_data_quality_report` with both weeks' dates. Note every warning/error.
3. `compare_periods` for the headline KPIs and the profit bridge.
4. `detect_anomalies` for the same periods. For each **high** anomaly, decide
   whether it explains something material and drill into it with the matching
   tool (campaigns, products, cost breakdown, refunds). Skip low-impact items
   but list them.
5. `compare_campaigns` and `compare_products` for the advertising and product sections.
6. `get_business_targets` and state which targets were met or missed.
7. Submit with this structure in mind (the report generator adds tables and charts):
   - **Executive summary** – 3–5 bullets, numbers cited.
   - **Revenue** – net sales, orders, AOV, discounts, refunds.
   - **Advertising** – spend, top campaign changes, platform ROAS (attribution caveat), blended MER.
   - **Product profitability** – mix and margin changes, missing costs.
   - **Refunds and fulfillment** – refund rate, fee categories, fees per order.
   - **Major changes and anomalies** – which ones matter and why (facts), possible reasons (hypotheses).
   - **Data-quality warnings** – everything that limits confidence.
   - **Recommended next steps** – specific, owned, evidence-linked.
