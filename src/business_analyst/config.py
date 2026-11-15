"""Configuration loaded from environment variables (and an optional `.env` file).

Every setting has a safe default so the offline commands (`doctor`, `init-db`,
`report --no-agent`, `eval`, `build-skill`, and the unit tests) work without
credentials. Only model-backed commands need `OPENAI_API_KEY`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REFERENCE_DATE = "2026-11-09"  # the fictional "today": a Monday


class MissingCredentialsError(RuntimeError):
    """Raised when a command needs credentials that are not configured."""


def find_project_root() -> Path:
    """Locate the repository root (the folder containing `data/` and `agent/`)."""
    override = os.environ.get("NORTHSTAR_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents, Path.cwd()]:
        if (candidate / "data").is_dir() and (candidate / "agent").is_dir():
            return candidate
    raise RuntimeError(
        "Could not find the project root (a folder containing 'data/' and 'agent/'). "
        "Run commands from the repository folder or set NORTHSTAR_PROJECT_ROOT."
    )


def _load_dotenv(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - declared dependency
        return
    load_dotenv(env_file, override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Period:
    """An inclusive date range in the reporting timezone."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Period end {self.end} is before start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @classmethod
    def parse(cls, start: str, end: str) -> Period:
        return cls(date.fromisoformat(start), date.fromisoformat(end))

    def label(self) -> str:
        return f"{self.start.isoformat()} to {self.end.isoformat()}"

    def as_dict(self) -> dict[str, object]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days}


@dataclass
class Settings:
    project_root: Path
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str = DEFAULT_MODEL
    openai_reasoning_effort: str | None = "low"
    max_iterations: int = 12
    max_tool_calls: int = 30
    warehouse_dir: Path | None = None
    sql_row_limit: int = 200
    sql_timeout_seconds: int = 5
    reporting_timezone: str = "America/New_York"
    reference_date: date = field(default_factory=lambda: date.fromisoformat(DEFAULT_REFERENCE_DATE))
    data_source: str = "mock"
    live_start_date: str = "2026-10-26"
    live_end_date: str = "2026-11-08"
    shopify_store_domain: str | None = None
    shopify_access_token: str | None = field(default=None, repr=False)
    shopify_api_version: str = "2026-10"
    meta_access_token: str | None = field(default=None, repr=False)
    meta_ad_account_id: str | None = None
    meta_api_version: str = "v26.0"
    google_spreadsheet_id: str | None = None
    google_service_account_file: str | None = None
    google_access_token: str | None = field(default=None, repr=False)
    google_ranges: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- paths
    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def scenarios_dir(self) -> Path:
        return self.data_dir / "scenarios"

    @property
    def agent_dir(self) -> Path:
        return self.project_root / "agent"

    @property
    def knowledge_dir(self) -> Path:
        return self.project_root / "knowledge-base"

    @property
    def sql_dir(self) -> Path:
        return self.project_root / "sql"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def warehouse(self) -> Path:
        return self.warehouse_dir or (self.project_root / "warehouse")

    def db_path(self, scenario: str | None = None) -> Path:
        name = "northstar.duckdb" if scenario in (None, "", "base", "profit_decline", "advertising") else (
            f"scenario_{scenario}.duckdb"
        )
        return self.warehouse / name

    # ----------------------------------------------------------- periods
    @property
    def current_week(self) -> Period:
        """The most recent complete Monday-Sunday week before the reference date."""
        ref = self.reference_date
        last_sunday = ref - timedelta(days=ref.weekday() + 1)
        return Period(last_sunday - timedelta(days=6), last_sunday)

    @property
    def previous_week(self) -> Period:
        cur = self.current_week
        return Period(cur.start - timedelta(days=7), cur.start - timedelta(days=1))

    # ------------------------------------------------------------ loading
    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = project_root or find_project_root()
        _load_dotenv(root)
        env = os.environ
        effort = env.get("OPENAI_REASONING_EFFORT", "low").strip().lower()
        source = env.get("DATA_SOURCE", "mock").strip().lower() or "mock"
        if source not in {"mock", "live"}:
            raise ValueError("DATA_SOURCE must be 'mock' or 'live'")
        warehouse = env.get("NORTHSTAR_WAREHOUSE_DIR", "").strip()
        warehouse_path = None
        if warehouse:
            warehouse_path = Path(warehouse).expanduser()
            if not warehouse_path.is_absolute():
                warehouse_path = root / warehouse_path
        return cls(
            project_root=root,
            openai_api_key=env.get("OPENAI_API_KEY") or None,
            openai_model=env.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
            openai_reasoning_effort=None if effort in {"", "none", "off"} else effort,
            max_iterations=_int_env("AGENT_MAX_ITERATIONS", 12),
            max_tool_calls=_int_env("AGENT_MAX_TOOL_CALLS", 30),
            warehouse_dir=warehouse_path,
            sql_row_limit=_int_env("SQL_ROW_LIMIT", 200),
            sql_timeout_seconds=_int_env("SQL_TIMEOUT_SECONDS", 5),
            reporting_timezone=env.get("REPORTING_TIMEZONE", "").strip() or "America/New_York",
            reference_date=date.fromisoformat(env.get("NORTHSTAR_REFERENCE_DATE", "").strip() or DEFAULT_REFERENCE_DATE),
            data_source=source,
            live_start_date=env.get("LIVE_START_DATE", "").strip() or "2026-10-26",
            live_end_date=env.get("LIVE_END_DATE", "").strip() or "2026-11-08",
            shopify_store_domain=env.get("SHOPIFY_STORE_DOMAIN") or None,
            shopify_access_token=env.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or None,
            shopify_api_version=env.get("SHOPIFY_API_VERSION", "").strip() or "2026-10",
            meta_access_token=env.get("META_ACCESS_TOKEN") or None,
            meta_ad_account_id=env.get("META_AD_ACCOUNT_ID") or None,
            meta_api_version=env.get("META_API_VERSION", "").strip() or "v26.0",
            google_spreadsheet_id=env.get("GOOGLE_SHEETS_SPREADSHEET_ID") or None,
            google_service_account_file=env.get("GOOGLE_SERVICE_ACCOUNT_FILE") or None,
            google_access_token=env.get("GOOGLE_SHEETS_ACCESS_TOKEN") or None,
            google_ranges={
                "product_costs": env.get("GOOGLE_SHEETS_PRODUCT_COSTS_RANGE") or "Product Costs!A1:F",
                "fulfillment_costs": env.get("GOOGLE_SHEETS_FULFILLMENT_RANGE") or "Fulfillment Costs!A1:F",
                "business_targets": env.get("GOOGLE_SHEETS_TARGETS_RANGE") or "Targets!A1:F",
            },
        )

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise MissingCredentialsError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key. "
                "Offline commands (doctor, init-db, report --no-agent, eval, build-skill) work without it."
            )
        return self.openai_api_key
