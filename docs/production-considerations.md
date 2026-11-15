# Production Considerations

This repository is a teaching project. Here is what would change before
using an AI analyst on real financial data.

## Secure database access
- Give the agent a dedicated **read-only** database role, not just a read-only
  connection mode. Restrict it to curated views (no raw PII tables).
- Keep the parameterized tools as the main interface; treat free-form SQL as
  an optional, audited capability with row limits and timeouts (as here).
- Store credentials in a secret manager, never in code or prompts. Rotate them.

## Data privacy
- Do not send customer names, emails or addresses to the model. Aggregate first.
- Check your LLM provider's data-retention settings (this project sends
  `store=False`) and your contracts/DPA.
- Logs contain tool arguments and summaries; treat them as confidential and expire them.

## Financial data validation
- Reconcile against systems of record every period (Shopify finance reports,
  Meta billing, 3PL invoices). Build those reconciliations as tests.
- Block reports when validation errors exist, or stamp them prominently.
- Have finance sign off on metric definitions and on each report before distribution.

## Metric governance
- One owner per metric definition; definitions versioned in Git
  (`knowledge-base/metric-definitions.md` here).
- Changes to formulas go through code review **and** a re-run of the eval suite.
- Distinguish illustrative metrics (contribution profit here) from accounting figures.

## Schema changes
- APIs change (Shopify releases quarterly API versions; Meta deprecates Graph
  versions). Pin versions, watch changelogs, and keep required-column checks
  so a renamed field fails loudly.
- Version the warehouse schema with migrations instead of rebuilding from scratch.

## Scheduled ingestion and data freshness
- Run ingestion on a schedule (cron, Airflow, Dagster, GitHub Actions…), write
  to a new snapshot, validate, then swap (the loader's atomic swap is the
  small-scale version of this).
- Track freshness per source; Meta data restates for days. Re-ingest a
  trailing window (e.g. last 7 days) every run.
- Show the "data as of" time on every report.

## Cost management
- Limit iterations and tool calls (configurable here). Use a smaller model for
  routine weekly reviews and a stronger one for investigations.
- Keep tool outputs compact; large JSON results cost tokens on every turn.
- Cache deterministic results; the report tables need no model at all.

## Logging and monitoring
- Keep trace IDs end to end (here: `logs/*.jsonl` and `logs/runs/<trace>.json`).
- Monitor tool error rates, guardrail warning rates, iteration-limit hits,
  token usage and eval pass rates over time.
- Alert on data-quality errors before anyone reads a report.

## Human review
- The agent drafts; people decide. Budget changes, supplier negotiations and
  pricing remain human decisions with the report as input.
- Make hypotheses visibly different from facts (the report labels each finding).
- Provide a feedback channel to correct wrong findings and add them as eval cases.

## Deployment
- Package as a scheduled job or internal service with SSO; no public endpoint.
- Separate environments (dev with fictional data, prod with real data).
- Pin dependencies and run the deterministic test suite in CI on every change.

## Operational reliability
- Retries with backoff for APIs (implemented in the adapters), circuit breakers
  for repeated failures, and clear "data unavailable" outputs instead of partial guesses.
- Run model-backed evals regularly; model updates can change behavior.
- Have a fallback: the facts-only report (`report --no-agent`) works without the model.
