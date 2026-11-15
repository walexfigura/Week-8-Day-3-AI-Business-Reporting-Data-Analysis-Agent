"""Claude Skill packaging and the command-line interface (offline commands only)."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from business_analyst.skill_builder import SkillValidationError, build_skill, parse_front_matter, validate_skill_md

ROOT = Path(__file__).resolve().parents[1]


def test_skill_front_matter_is_valid():
    fm = validate_skill_md(ROOT / "claude-skill/business-analyst/SKILL.md")
    assert fm["name"] == "business-analyst" and len(fm["description"]) <= 1024


@pytest.mark.parametrize("bad", [
    "no front matter",
    "---\nname: Business Analyst\ndescription: x\n---\n",
    "---\nname: claude-analyst\ndescription: x\n---\n",
    "---\nname: business-analyst\n---\n",
])
def test_invalid_front_matter_rejected(tmp_path, bad):
    path = tmp_path / "SKILL.md"
    path.write_text(bad)
    with pytest.raises(SkillValidationError):
        validate_skill_md(path)


def test_build_skill_zip(settings, base_db, tmp_path):
    zip_path = build_skill(settings, out_dir=tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert zf.testzip() is None
        skill_md = zf.read("business-analyst/SKILL.md").decode()
    assert all(n.startswith("business-analyst/") for n in names)
    for required in ("references/metric-definitions.md", "references/setup.md", "references/tool-configuration.md",
                     "assets/sample-data/daily-summary.csv", "scripts/kpi_calculator.py"):
        assert f"business-analyst/{required}" in names
    assert parse_front_matter(skill_md)["name"] == "business-analyst"


def test_skill_kpi_script_matches_repository(base_db):
    out = subprocess.run([sys.executable, "-I", "claude-skill/business-analyst/scripts/kpi_calculator.py",
                          "claude-skill/business-analyst/assets/sample-data/daily-summary.csv",
                          "--previous", "2026-10-26", "2026-11-01", "--current", "2026-11-02", "2026-11-08"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert "$18,600.00" in out and "-38.0%" in out and "reconciles: True" in out


def test_skill_script_treats_missing_day_as_missing(tmp_path):
    src = (ROOT / "claude-skill/business-analyst/assets/sample-data/daily-summary.csv").read_text().splitlines()
    gap = tmp_path / "gap.csv"
    gap.write_text("\n".join(line for line in src if not line.startswith("2026-11-08")) + "\n")
    out = subprocess.run([sys.executable, "-I", str(ROOT / "claude-skill/business-analyst/scripts/kpi_calculator.py"),
                          str(gap), "--previous", "2026-10-26", "2026-11-01", "--current", "2026-11-02", "2026-11-08"],
                         capture_output=True, text=True, check=True).stdout
    assert "no data for 2026-11-08" in out and "unavailable" in out


def run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "OPENAI_API_KEY": "", **(env_extra or {})}
    return subprocess.run([sys.executable, "run.py", *args], cwd=ROOT, capture_output=True, text=True, env=env, timeout=180)


def test_cli_help_lists_documented_commands():
    out = run_cli("--help").stdout
    for cmd in ("doctor", "init-db", "demo", "analyze", "report", "eval", "build-skill"):
        assert cmd in out


def test_cli_doctor_offline(tmp_path):
    r = run_cli("doctor", env_extra={"NORTHSTAR_WAREHOUSE_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "contribution profit −38% (scenario)" in r.stdout and "[FAIL]" not in r.stdout


def test_cli_analyze_without_key_explains(tmp_path):
    r = run_cli("analyze", "--scenario", "profit_decline", env_extra={"NORTHSTAR_WAREHOUSE_DIR": str(tmp_path)})
    assert r.returncode == 2 and "OPENAI_API_KEY is not set" in r.stdout


def test_cli_sql_rejects_writes(tmp_path):
    r = run_cli("sql", "DELETE FROM orders", env_extra={"NORTHSTAR_WAREHOUSE_DIR": str(tmp_path)})
    assert r.returncode == 1 and "Rejected" in r.stdout


def test_cli_tool_runs_offline(tmp_path):
    r = run_cli("tool", "get_contribution_profit", "--args", '{"start_date":"2026-11-02","end_date":"2026-11-08"}',
                env_extra={"NORTHSTAR_WAREHOUSE_DIR": str(tmp_path)})
    assert r.returncode == 0 and '"contribution_profit": 18600.0' in r.stdout
