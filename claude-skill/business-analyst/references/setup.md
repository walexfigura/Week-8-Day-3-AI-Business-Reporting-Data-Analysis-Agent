# Setup

## What this Skill is (and is not)

A Skill is a folder of instructions, reference documents, sample data and
scripts that Claude loads when a task matches its description. It teaches
Claude *how* to analyze business data. It does **not** create database
access, API connections or integrations by itself.

## Install

1. Get `business-analyst-skill.zip` (build it from the companion repository
   with `python run.py build-skill`; it is written to `dist/`).
2. **Claude apps (claude.ai / desktop):** open Settings → Capabilities (the
   exact menu name can change), make sure code execution / file creation is
   enabled, then upload the ZIP under Skills. Enable the Skill.
3. **Claude Code:** unzip into `~/.claude/skills/` (personal) or
   `.claude/skills/` inside a project, so the path is
   `.claude/skills/business-analyst/SKILL.md`.
4. **Claude API:** upload the folder through the Skills API and reference the
   skill in your request with the code-execution tool enabled. See Anthropic's
   Agent Skills documentation for the current request format.

## First run (Mode A, no connectors)

Ask: *"Why did our contribution profit decrease last week?"*

Claude should say it is using fictional Northstar Supply sample data, run
`scripts/kpi_calculator.py` on `assets/sample-data/daily-summary.csv`, and
explain: net sales +20.0%, Meta spend +80.0%, contribution profit −38.0%, a
bridge that sums to −$11,400, the Holiday Gift Push campaign as the largest
spend increase, and hypotheses labelled as such.

## Using your own files

Upload CSV/Excel exports. The fastest path is a **daily summary** with the
columns listed in `scripts/kpi_calculator.py` (one row per date). Claude can
also build that summary from raw exports (orders, line items, refunds, ad
platform daily rows, cost sheets) — expect it to ask about missing costs,
timezones and refund timing first.

## Customizing

- Change metric definitions in `references/metric-definitions.md` (keep the
  "missing values" rules).
- Replace `references/business-context.md` with your company's context.
- Replace the sample data in `assets/sample-data/` or delete it.
- Keep `SKILL.md` short; put detail in `references/`.
