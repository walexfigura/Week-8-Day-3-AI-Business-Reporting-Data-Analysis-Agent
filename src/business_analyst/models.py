"""Shared data structures (plain dataclasses — no heavy frameworks needed)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
FindingKind = Literal["observed_fact", "calculation", "hypothesis"]


@dataclass
class DataQualityIssue:
    """Something the ingestion pipeline or a validation query noticed."""

    source: str  # e.g. "shopify/orders.csv"
    check: str  # e.g. "duplicate_key"
    severity: Severity
    message: str
    record_ref: str = ""
    count: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCallRecord:
    step: int
    evidence_id: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    summary: str
    error_code: str | None = None
    output: dict[str, Any] | None = None


@dataclass
class Finding:
    statement: str
    kind: FindingKind
    evidence_ids: list[str] = field(default_factory=list)
    how_to_verify: str = ""


@dataclass
class Recommendation:
    action: str
    rationale: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """The structured answer the agent submits, plus everything needed to audit it."""

    question: str
    trace_id: str
    status: Literal["completed", "iteration_limit", "error"]
    headline: str = ""
    answer: str = ""
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    guardrail_warnings: list[str] = field(default_factory=list)
    model: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    def as_dict(self, include_outputs: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_outputs:
            for call in data["tool_calls"]:
                call.pop("output", None)
        return data
