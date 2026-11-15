#!/usr/bin/env python3
"""Build claude-skill/business-analyst-skill.zip (same as `python run.py build-skill`).

Validates SKILL.md front matter, refreshes the Skill's sample data from the
DuckDB database, syncs shared knowledge files into references/, verifies the
bundled KPI script reproduces the scenario totals, and zips the folder with a
single top-level `business-analyst/` directory.
Logic: src/business_analyst/skill_builder.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from business_analyst.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["build-skill"]))
