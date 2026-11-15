#!/usr/bin/env python3
"""Entry point: `python run.py <command>`. Run `python run.py --help` for commands.

Adds `src/` to the import path so the project works straight from a clone,
even before (or without) `pip install -e .`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

try:
    from business_analyst.cli import main
except ModuleNotFoundError as exc:  # most often: virtual environment not activated
    print(f"Missing Python package: {exc.name}")
    print('Activate your virtual environment and run:  pip install -e ".[dev]"')
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
