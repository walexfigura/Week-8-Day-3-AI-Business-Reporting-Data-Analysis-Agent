# Workflow: Report generation

The report itself (tables, charts, JSON) is generated **deterministically** by
`python run.py report` from the same calculations the tools use, so tables and
charts cannot disagree with each other. Your job is the narrative.

## Your narrative (submitted via `submit_analysis`)

- `headline`: one sentence with the most important change and its main driver.
- `answer`: 3–6 short paragraphs or bullets in Markdown. Lead with the answer.
- `findings`: each with `kind` (`observed_fact`, `calculation`, `hypothesis`)
  and `evidence_ids`. Hypotheses need `how_to_verify`.
- `data_gaps`: everything that limits confidence (missing costs, missing days,
  duplicates removed, no order-level attribution, other channels not modeled,
  contribution profit ≠ net income).
- `recommendations`: specific next steps with an owner-friendly action, a
  rationale and evidence IDs.

## Consistency rules

- Use numbers exactly as tools returned them (rounded for prose at most).
- The guardrail checks every number in your findings and answer against tool
  outputs; ungrounded numbers are flagged in the report.
- Do not restate a number with a different value elsewhere (e.g. -38% in one
  place and -37% in another).
- Use the same period labels as the tools.
