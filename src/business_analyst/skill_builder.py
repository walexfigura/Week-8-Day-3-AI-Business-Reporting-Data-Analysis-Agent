"""Build the Claude Skill package: claude-skill/business-analyst-skill.zip.

Steps:
1. Validate SKILL.md front matter (name/description rules from the Agent Skills spec).
2. Refresh the Skill's compact sample data from the DuckDB database, so the
   numbers in the Skill match this repository exactly (14 daily rows instead
   of ~6,000 order lines).
3. Sync shared knowledge files (metric definitions, company context,
   reporting rules) into references/ so there is one source of truth.
4. Verify the bundled KPI script reproduces the scenario totals.
5. Zip everything under a single top-level `business-analyst/` folder.

The ZIP is committed to the repository so it can be downloaded directly. It is
built reproducibly (sorted entries, fixed timestamps and permissions), so
rebuilding produces an identical file unless the Skill content changed.
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from .config import Settings
from .database import connect_readonly, fetch_dicts, to_jsonable
from .ingestion.loader import ensure_database

SKILL_NAME = "business-analyst"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SYNCED = {
    "metric-definitions.md": "metric-definitions.md",
    "company-context.md": "business-context.md",
    "reporting-rules.md": "reporting-rules.md",
}
SYNC_NOTE = ("<!-- Synced from knowledge-base/{src} by scripts/build_skill.py. Edit the original, not this copy. "
             "Mentions of Python modules and tools refer to the companion repository; inside the Skill, apply the "
             "same formulas with code execution or the bundled scripts/kpi_calculator.py. -->\n\n")


class SkillValidationError(ValueError):
    pass


def parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise SkillValidationError("SKILL.md must start with YAML front matter ('---' on the first line).")
    end = text.find("\n---", 4)
    if end == -1:
        raise SkillValidationError("SKILL.md front matter is not closed with '---'.")
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise SkillValidationError(f"Front matter line is not 'key: value': {line!r}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def validate_skill_md(path: Path) -> dict[str, str]:
    fm = parse_front_matter(path.read_text(encoding="utf-8"))
    name, description = fm.get("name", ""), fm.get("description", "")
    problems = []
    if not name or len(name) > 64 or not NAME_RE.match(name):
        problems.append("name must be 1-64 chars of lowercase letters, digits and hyphens")
    if "anthropic" in name or "claude" in name:
        problems.append("name must not contain reserved words 'anthropic' or 'claude'")
    if name != SKILL_NAME:
        problems.append(f"name must be '{SKILL_NAME}' (matches the folder name)")
    if not description or len(description) > 1024:
        problems.append("description is required and must be at most 1024 characters")
    if re.search(r"<[^>]+>", name + description):
        problems.append("name/description must not contain XML tags")
    if problems:
        raise SkillValidationError("Invalid SKILL.md front matter: " + "; ".join(problems))
    return fm


# ----------------------------------------------------------------------------- sample data
SAMPLE_QUERIES = {
    "daily-summary.csv": """
        SELECT day AS date, orders, units, gross_sales, discounts, refunds, net_sales,
               cogs_known AS cogs, f.pick_pack, f.packaging, f.shipping_labels, f.payment_processing, ad_spend
        FROM v_daily_contribution AS c JOIN v_daily_fees AS f USING (day)
        ORDER BY day""",
    "campaign-daily.csv": """
        SELECT day AS date, campaign_id, campaign_name, spend, impressions, clicks,
               platform_purchases AS meta_reported_purchases, platform_purchase_value AS meta_reported_purchase_value
        FROM v_campaign_daily ORDER BY day, campaign_id""",
    "campaigns.csv": """
        SELECT campaign_id, campaign_name, objective, status, start_date, stop_date, daily_budget, product_focus, notes
        FROM ad_campaigns ORDER BY campaign_id""",
    "product-weekly.csv": """
        SELECT CASE WHEN day < DATE '2026-11-02' THEN 'previous (2026-10-26..2026-11-01)'
                    ELSE 'current (2026-11-02..2026-11-08)' END AS week,
               sku, SUM(units) AS units, SUM(gross_sales) AS gross_sales, SUM(discounts) AS discounts,
               SUM(net_line_sales) AS net_line_sales_before_refunds, SUM(cogs_known) AS cogs
        FROM v_product_daily GROUP BY 1, 2 ORDER BY 1 DESC, 2""",
    "refunds-weekly.csv": """
        SELECT CASE WHEN day < DATE '2026-11-02' THEN 'previous (2026-10-26..2026-11-01)'
                    ELSE 'current (2026-11-02..2026-11-08)' END AS week,
               sku, SUM(refund_count) AS refund_count, SUM(refunds) AS refunds
        FROM v_product_refunds GROUP BY 1, 2 ORDER BY 1 DESC, 2""",
    "product-costs.csv": """
        SELECT sku, product_name, unit_cost, effective_from, effective_to, notes FROM product_costs ORDER BY sku, effective_from""",
    "business-targets.csv": "SELECT metric, target_value, unit, direction, notes FROM business_targets ORDER BY metric",
}


def export_sample_data(settings: Settings, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    con = connect_readonly(ensure_database(settings, None))
    written = []
    try:
        for name, sql in SAMPLE_QUERIES.items():
            rows = fetch_dicts(con, sql)
            path = dest / name
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh, lineterminator="\n")
                writer.writerow(list(rows[0].keys()))
                for r in rows:
                    writer.writerow(["" if v is None else to_jsonable(v) for v in r.values()])
            written.append(path)
    finally:
        con.close()
    return written


def verify_kpi_script(skill_dir: Path) -> str:
    script = skill_dir / "scripts" / "kpi_calculator.py"
    data = skill_dir / "assets" / "sample-data" / "daily-summary.csv"
    out = subprocess.run([sys.executable, "-I", str(script), str(data), "--previous", "2026-10-26", "2026-11-01",
                          "--current", "2026-11-02", "2026-11-08"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SkillValidationError(f"kpi_calculator.py failed: {out.stderr.strip()[:300]}")
    for expected in ("+20.0%", "+80.0%", "-38.0%", "reconciles: True"):
        if expected not in out.stdout:
            raise SkillValidationError(f"kpi_calculator.py output is missing {expected!r}:\n{out.stdout}")
    return out.stdout


def build_skill(settings: Settings, out_dir: Path | None = None) -> Path:
    skill_dir = settings.project_root / "claude-skill" / SKILL_NAME
    validate_skill_md(skill_dir / "SKILL.md")
    export_sample_data(settings, skill_dir / "assets" / "sample-data")
    for src, dst in SYNCED.items():
        text = (settings.knowledge_dir / src).read_text(encoding="utf-8")
        (skill_dir / "references" / dst).write_text(SYNC_NOTE.format(src=src) + text, encoding="utf-8")
    verify_kpi_script(skill_dir)
    # Every reference mentioned in SKILL.md must exist in the package.
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for ref in re.findall(r"`((?:references|assets|scripts)/[^`]+)`", skill_text):
        if not (skill_dir / ref).exists():
            raise SkillValidationError(f"SKILL.md references {ref}, which does not exist")
    out_dir = out_dir or (settings.project_root / "claude-skill")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{SKILL_NAME}-skill.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.name != ".DS_Store":
                # Fixed timestamp and permissions -> identical bytes on every rebuild.
                info = zipfile.ZipInfo((Path(SKILL_NAME) / path.relative_to(skill_dir)).as_posix(),
                                       date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, path.read_bytes())
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if zf.testzip() is not None or f"{SKILL_NAME}/SKILL.md" not in names:
            raise SkillValidationError("ZIP verification failed")
        if any(not n.startswith(f"{SKILL_NAME}/") for n in names):
            raise SkillValidationError("ZIP must contain a single top-level folder")
    return zip_path
