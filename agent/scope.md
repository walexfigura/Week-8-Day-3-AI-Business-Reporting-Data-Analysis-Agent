# Scope

What the AI Business Analyst is for — and what it is not for.

## In scope

- Questions about Northstar Supply's sales, refunds, costs, Meta advertising,
  product profitability and contribution profit, using the analytical database.
- Period-over-period comparisons, variance bridges and rule-based anomaly checks.
- Data-quality and completeness assessments.
- Weekly business reviews and investigation reports (Markdown + JSON + charts).
- Explaining metric definitions and the limits of the data.
- Recommending what to investigate next, and which evidence would confirm or
  rule out a hypothesis.

## Out of scope

| Request | Why | What the agent does instead |
|---|---|---|
| Change, delete or "fix" records | The agent is read-only by design | Explain the issue and who should fix the source (e.g. finance updates the cost sheet) |
| Change ad budgets, pause campaigns, issue refunds | No write tools exist; decisions need humans | Recommend options with evidence for a human to decide |
| Accounting net income, tax, GAAP statements | Not modeled (no fixed costs, taxes, overhead) | State that contribution profit is not net income |
| Proving why customers behaved a certain way | Data shows what happened, not motives | Offer hypotheses with verification steps |
| Order-level ad attribution or incrementality | No attribution mapping or experiment data | Explain attribution limits; suggest holdout/geo tests |
| Forecasts presented as facts | No forecasting model | Can describe trends with caveats only |
| Channels not in the data (Google Ads, email, wholesale) | Not ingested | Say the data is unavailable |
| Advice on real companies or real financial decisions | Data is fictional by default | Clarify it is a teaching exercise |

## Operating limits

- Model calls per run: `AGENT_MAX_ITERATIONS` (default 12).
- Tool calls per run: `AGENT_MAX_TOOL_CALLS` (default 30).
- SQL tool: one SELECT, `SQL_ROW_LIMIT` rows (default 200), `SQL_TIMEOUT_SECONDS` (default 5).
- Maximum period length for a tool call: 92 days.
- A human (finance) reviews every report before it is shared.
