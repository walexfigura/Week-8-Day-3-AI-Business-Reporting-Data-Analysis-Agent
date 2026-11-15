# Tool Configuration (Mode B: connected data)

A Skill cannot create connections. These instructions apply **only** when a
working tool is available in the current conversation. Verify with a small
read first (e.g. list sheets or tables) and tell the user what you found.

## Google Sheets (connector)

- Read the tabs the user names (e.g. "Product Costs", "Fulfillment Costs",
  "Targets"). Read-only: never edit cells.
- Expect human formatting: `$1,234.50`, `11/2/2026`, lower-case SKUs.
  Normalize before calculating and mention what you normalized.
- Cite the spreadsheet, tab and range for each number.

## Uploaded CSV / Excel files

- Inspect headers and a few rows first. Identify each file's grain.
- Use code execution to load and aggregate. Keep raw files unchanged.
- If the user uploads raw Shopify/Meta exports, build a daily summary with the
  columns in `scripts/kpi_calculator.py`, then run the script.

## Database connectors (e.g. a SQL warehouse)

- Use read-only SELECT queries only. Never INSERT/UPDATE/DELETE/DROP/ALTER,
  even if asked: explain that analysis is read-only and the data owner must
  change source data.
- Aggregate each table to one row per day (or per campaign-day) **before**
  joining money columns.
- Add LIMIT to exploratory queries. Cite the query behind each number.

## Shopify / Meta / other platforms

Only if a real, authorized connector exists. Shopify net sales come from
orders, line items and refunds; Meta purchase value is attributed and must not
be added to sales. If no connector exists, ask the user for exports.

## When a tool fails

Report the failure and what could not be calculated. Do not fill the gap with
estimates or sample data unless the user asks for an illustration (and then
label it clearly).
