"""Shared fixtures. Tests build their own DuckDB files in a temporary folder,
so they never touch your warehouse/ directory and never call external APIs."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from business_analyst.config import Settings
from business_analyst.database import connect_readonly
from business_analyst.ingestion.loader import build_database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    base = Settings.from_env(project_root=ROOT)
    return dataclasses.replace(base, warehouse_dir=tmp_path_factory.mktemp("warehouse"), data_source="mock",
                               openai_api_key=None)


@pytest.fixture(scope="session")
def base_db(settings: Settings) -> Path:
    return build_database(settings, None).db_path


@pytest.fixture()
def con(base_db: Path):
    connection = connect_readonly(base_db)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def scenario_db(settings: Settings):
    cache: dict[str, Path] = {}

    def get(name: str) -> Path:
        if name not in cache:
            cache[name] = build_database(settings, name).db_path
        return cache[name]

    return get
