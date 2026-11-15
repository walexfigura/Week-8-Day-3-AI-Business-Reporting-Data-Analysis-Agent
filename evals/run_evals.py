#!/usr/bin/env python3
"""Run the evaluation suite (same as `python run.py eval`).

    python evals/run_evals.py            # deterministic cases only (no API key needed)
    python evals/run_evals.py --agent    # also model-backed cases (needs OPENAI_API_KEY)
    python evals/run_evals.py --case sql --verbose

Logic lives in src/business_analyst/evaluation.py; cases in evals/test-cases.json.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from business_analyst.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["eval", *sys.argv[1:]]))
