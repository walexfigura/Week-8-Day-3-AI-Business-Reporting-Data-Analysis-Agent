# Troubleshooting

Start with `python run.py doctor`. It checks Python, packages, data files,
builds the database, verifies the scenario totals and tests the SQL guardrail.

| Symptom | Cause | Fix |
|---|---|---|
| `Missing Python package: duckdb` | Virtual environment not active or deps not installed | Activate `.venv` (see README), then `pip install -e ".[dev]"` |
| `python: command not found` / wrong version | Python 3.10+ not on PATH | macOS/Linux: try `python3`; Windows: `py -3.11` |
| `OPENAI_API_KEY is not set` | No key in `.env` | `cp .env.example .env` (Windows: `copy .env.example .env`) and add the key. Offline commands work without it |
| `openai.AuthenticationError` / 401 | Wrong or revoked key | Create a new key; check there are no spaces or quotes around it in `.env` |
| `model not found` / 404 from OpenAI | `OPENAI_MODEL` not available to your account | Set `OPENAI_MODEL` to a model your project can use that supports function calling |
| `reasoning` parameter error | Non-reasoning model with `OPENAI_REASONING_EFFORT` set | Set `OPENAI_REASONING_EFFORT=none` |
| Rate limit / 429 | Too many requests or low quota | Wait and retry; check your usage limits |
| `No database at warehouse/...` | Database not built yet | `python run.py init-db` (most commands build it automatically) |
| `IO Error: Could not set lock on file` | Another process has the DuckDB file open for writing | Close other Python sessions/DB browsers, then retry |
| `... is missing required column(s)` | A CSV header was renamed | Restore the header or regenerate data: `python scripts/generate_data.py` |
| Numbers changed after editing CSVs | Expected — the database rebuilds when sources change | Run `python run.py doctor`; the scenario checks will show whether the 20/80/−38 story still holds |
| `Unknown scenario` | Typo | `python run.py scenarios` lists valid names |
| SQL `Rejected: ...` | The read-only guardrail blocked the query | Use one SELECT on known tables; see `docs/understanding-sql.md` |
| SQL `timeout` | Query too heavy (e.g. accidental cross join) | Add join conditions, aggregate first |
| Matplotlib font-cache message on first run | One-time cache build | Ignore; it happens once |
| Charts missing from a report | Contribution profit incomplete (no bridge is drawn from incomplete data) | Read the data-quality section of the report |
| Eval shows agent cases `SKIP` | No `--agent` flag or no key | `python run.py eval --agent` with `OPENAI_API_KEY` set |
| Agent eval fails intermittently | Model output varies | Inspect `evals/results/<run>.json` and `logs/runs/<trace>.json`; tighten prompts/workflows |
| Live mode errors (401/403) | Token lacks scopes, or the sheet is not shared | See `docs/connecting-real-data.md` → permissions |
| Windows: `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or use `.venv\Scripts\activate.bat` in cmd |
| Windows: timezone errors | Missing IANA timezone data | `pip install tzdata` (installed automatically on Windows by `pip install -e .`) |

## Inspecting a run

- `logs/YYYY-MM-DD.jsonl` – one JSON event per line (`run_started`, `tool_call`, `submission`, `run_finished`) with trace IDs.
- `logs/runs/<trace>.json` – the full analysis including every tool output.
- `reports/<name>/summary.json` – every figure in the report, the chart data, and consistency-check results.
- `evals/results/<run>.json` – per-check eval results.

## Resetting

Delete `warehouse/`, `reports/*` (keep `.gitkeep`), `logs/` and `evals/results/`; then `python run.py init-db --all`.
