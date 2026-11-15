# Reporting Rules

## Reporting periods

- Standard period: **Monday–Sunday week** in the reporting timezone.
- "Last week" means the most recent **complete** Monday–Sunday week before
  today. In the fictional dataset, today is **Monday 2026-11-09**, so:
  - last week (current period) = **2026-11-02 to 2026-11-08**
  - the week before (previous period) = **2026-10-26 to 2026-11-01**
- Never include a partial week (e.g. today) in a weekly comparison.

## Timezone

- All reporting dates use **America/New_York**.
- Shopify timestamps are stored in UTC and converted at ingestion. An order
  placed at 9:30 pm Sunday in New York is 01:30 Monday UTC — it still belongs
  to Sunday.
- Daylight saving time ended on 2026-11-01; conversion uses the IANA
  timezone database, so this is handled automatically.

## Currency

- All amounts are **USD**. Records in other currencies are rejected at
  ingestion (no foreign-exchange conversion is modeled).

## Rounding

- Calculate with full precision (DECIMAL in DuckDB, `Decimal` in Python).
- Round only for display: money to 2 decimals (`$18,600.00`), or to whole
  dollars in narrative text when exact cents add nothing (`$18,600`).
- Percentages: 1 decimal in narrative (`-38.0%`), 2 decimals in tables.
- Percentage-point changes are written "points" (`-14.5 points`), not "%".

## Date comparisons

- Compare periods of **equal length**. If someone asks for unequal periods,
  run the comparison but say so prominently and compare daily averages.
- Prefer comparing the same weekdays (week vs. week).
- Report both absolute and percentage changes.

## Missing-data disclosure

Every answer and report must state:

- Whether every source covers every day of each period (coverage check).
- Any SKU without a product cost and the units affected.
- Any duplicates removed or rows rejected during ingestion.
- What could not be calculated because of the above.

Wording: "Contribution profit cannot be calculated for 2026-11-02 to 2026-11-08
because 368 units of NS-BTL-40 have no product cost" — not "contribution profit
was $X" with a footnote.

## Evidence requirements

- Every number in an answer must come from a tool result and cite its evidence
  ID (e.g. `[E3]`). Reports list the tool, arguments and period behind each table.
- Label each finding as one of:
  - **Observed fact** — read directly from data ("Meta spend was $36,000").
  - **Calculation** — deterministic arithmetic on data ("the bridge reconciles to −$11,400").
  - **Hypothesis** — a possible explanation that needs more evidence, with how to verify it.

## Report formatting

- Lead with the answer (executive summary in 3–5 bullets).
- Tables for numbers, prose for interpretation.
- Use the same figures in tables, charts and narrative (reports are generated
  from one calculation so they cannot disagree).
- Show the reporting periods, timezone, currency, data source mode (mock/live)
  and scenario on every report.
- Mark fictional data as fictional.
