# Workflow: Product profitability

Use for questions about which products make money, product mix, COGS changes or missing costs.

## Definitions

- Product net sales = line sales − discounts − refunds (refunds by refund date, mapped to SKU through the refunded line).
- Product margin = product net sales − COGS. **Ads and fulfillment are not
  allocated to products** (no valid attribution), so this is not product-level
  contribution profit — say so.

## Steps

1. `get_data_quality_report` – look for `lines_missing_product_cost` and `unknown_skus`.
2. `get_product_profitability` for the period(s): units, net sales, COGS, margin %, share of sales.
3. `compare_products` between periods: mix shift (share of net sales) and margin changes.
4. If total COGS % of net sales changed, explain it with:
   - **mix** (more sales of lower-margin SKUs), and/or
   - **unit-cost changes** (check product-costs effective dates via `run_readonly_sql`
     on `product_costs`), and/or
   - **discounting** (lower net price per unit).
5. If a SKU has missing costs: do not report its margin or total COGS as complete.
   Quantify units and sales affected and recommend updating the cost sheet.
6. Compare margins with the fictional `product_gross_margin` target.
