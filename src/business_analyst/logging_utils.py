"""Structured JSONL logging with trace IDs.

Every agent run, report and evaluation gets a trace ID. Each event is one JSON
line in ``logs/<date>.jsonl`` so you can reconstruct exactly which tools were
called, with which arguments, and what they returned (summarized).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_trace_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


class JsonlLogger:
    def __init__(self, log_dir: Path, echo: bool = False):
        self.log_dir = log_dir
        self.echo = echo

    @property
    def path(self) -> Path:
        return self.log_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"

    def log(self, trace_id: str, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "trace_id": trace_id,
                  "event": event, **fields}
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=_default) + "\n")
        except OSError:  # logging must never break an analysis
            pass
        if self.echo:
            print(f"[{event}] " + json.dumps(fields, default=_default)[:300])


class NullLogger(JsonlLogger):
    def __init__(self) -> None:  # noqa: D401 - intentionally does nothing
        super().__init__(Path("."), echo=False)

    def log(self, trace_id: str, event: str, **fields: Any) -> None:
        return None
