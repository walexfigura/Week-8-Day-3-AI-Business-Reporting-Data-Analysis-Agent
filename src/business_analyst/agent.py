"""The AI Business Analyst agent loop: OpenAI Responses API + function calling.

One run:

    question ──► first message (periods, reference date, data mode)
             ──► model decides which tools to call            ◄──┐
             ──► Python runs tools on a READ-ONLY DuckDB         │ repeat until
             ──► results (with evidence IDs) go back to model ───┘ submit_analysis
             ──► deterministic guardrails check the submission
                 (one revision round if citations/numbers are off)
             ──► AnalysisResult (+ JSONL log with a trace ID)

Nothing here hard-codes "call X, then Y". The model chooses the investigation
path; Python does the math; guardrails keep the narrative tied to evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .database import connect_readonly
from .guardrails import check_submission
from .ingestion.loader import ensure_database, load_scenario
from .logging_utils import JsonlLogger, new_trace_id
from .models import AnalysisResult, Finding, Recommendation, ToolCallRecord
from .tools.registry import ToolContext, ToolRegistry, build_registry, summarize_result

SUBMIT_TOOL = "submit_analysis"
MAX_REVISIONS = 1

SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "name": SUBMIT_TOOL,
    "description": ("Submit the final analysis. Call exactly once, after investigating. Every number must come from "
                    "a tool result; cite evidence IDs."),
    "strict": True,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "answer", "findings", "data_gaps", "recommendations"],
        "properties": {
            "headline": {"type": "string", "description": "One sentence: the main change and its main driver."},
            "answer": {"type": "string", "description": "Markdown answer for a business reader, citing [E#]."},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["statement", "kind", "evidence_ids", "how_to_verify"],
                    "properties": {
                        "statement": {"type": "string"},
                        "kind": {"type": "string", "enum": ["observed_fact", "calculation", "hypothesis"]},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "how_to_verify": {"type": "string", "description": "Required for hypotheses; empty otherwise."},
                    },
                },
            },
            "data_gaps": {"type": "array", "items": {"type": "string"}},
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "rationale", "evidence_ids"],
                    "properties": {
                        "action": {"type": "string"},
                        "rationale": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
}


def _to_input_item(item: Any) -> dict[str, Any]:
    """Convert a response output item back into an input item for the next turn."""
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    if isinstance(item, dict):
        return dict(item)
    return dict(vars(item))


def build_first_message(question: str, settings: Settings, scenario: dict[str, Any], data_source: str) -> str:
    cur, prev = settings.current_week, settings.previous_week
    return (
        f"Business question: {question}\n\n"
        "Context:\n"
        f"- Today (reference date): {settings.reference_date.isoformat()}\n"
        f"- 'Last week' / current period: {cur.start} to {cur.end}\n"
        f"- 'The week before' / previous period: {prev.start} to {prev.end}\n"
        f"- Reporting timezone: {settings.reporting_timezone}; currency: USD\n"
        f"- Data source: {data_source} ({'FICTIONAL sample data' if data_source == 'mock' else 'live API data'}); "
        f"scenario: {scenario['name']}\n\n"
        "Investigate with the tools (start with read_workflow for the matching workflow), then call submit_analysis."
    )


class BusinessAnalystAgent:
    def __init__(self, settings: Settings, *, client: Any | None = None, registry: ToolRegistry | None = None,
                 logger: JsonlLogger | None = None):
        self.settings = settings
        self.registry = registry or build_registry()
        self._client = client
        self.logger = logger or JsonlLogger(settings.logs_dir)
        self.instructions = (settings.agent_dir / "system-prompt.md").read_text(encoding="utf-8")

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # lazy import: offline commands never need it

            self._client = OpenAI(api_key=self.settings.require_openai_key(), max_retries=2, timeout=120)
        return self._client

    def _create_response(self, input_items: list[dict[str, Any]]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.settings.openai_model,
            "instructions": self.instructions,
            "input": input_items,
            "tools": [*self.registry.openai_tools(), SUBMIT_SCHEMA],
            "tool_choice": "required",  # every turn is a tool call; the last one is submit_analysis
            "parallel_tool_calls": True,
            "store": False,
        }
        if self.settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.settings.openai_reasoning_effort}
            kwargs["include"] = ["reasoning.encrypted_content"]  # needed with store=False
        return self.client.responses.create(**kwargs)

    # ------------------------------------------------------------------ run
    def run(self, question: str, *, scenario: str | None = None, fault_injection: dict[str, str] | None = None,
            trace_id: str | None = None, db_path: Path | None = None) -> AnalysisResult:
        settings = self.settings
        trace_id = trace_id or new_trace_id("analysis")
        scenario_info = load_scenario(settings, scenario)
        result = AnalysisResult(question=question, trace_id=trace_id, status="error", model=settings.openai_model)
        log = self.logger.log
        try:
            path = db_path or ensure_database(settings, scenario)
            con = connect_readonly(path)
        except Exception as exc:  # database missing/corrupt: report, never fabricate
            result.error = f"Database unavailable: {exc}"
            log(trace_id, "run_failed", error=result.error)
            return result
        ctx = ToolContext(con=con, settings=settings, scenario=scenario_info["name"], trace_id=trace_id,
                          fault_injection=dict(fault_injection or {}))
        log(trace_id, "run_started", question=question, scenario=scenario_info["name"], model=settings.openai_model,
            data_source=settings.data_source, fault_injection=ctx.fault_injection)

        input_items: list[dict[str, Any]] = [
            {"role": "user", "content": build_first_message(question, settings, scenario_info, settings.data_source)}
        ]
        evidence: dict[str, dict[str, Any]] = {}
        submission: dict[str, Any] | None = None
        revisions = 0
        tool_calls = 0
        result.status = "iteration_limit"
        try:
            for iteration in range(1, settings.max_iterations + 1):
                log(trace_id, "model_request", iteration=iteration, input_items=len(input_items))
                response = self._create_response(input_items)
                result.model_calls += 1
                usage = getattr(response, "usage", None)
                if usage is not None:
                    result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    result.output_tokens += getattr(usage, "output_tokens", 0) or 0
                calls = [o for o in response.output if getattr(o, "type", None) == "function_call"]
                input_items.extend(_to_input_item(o) for o in response.output)
                if not calls:
                    input_items.append({"role": "user", "content": f"Call {SUBMIT_TOOL} to finish."})
                    continue
                finished = False
                for call in calls:
                    if call.name == SUBMIT_TOOL:
                        output, accepted = self._review_submission(call.arguments, evidence, revisions, result)
                        log(trace_id, "submission", accepted=accepted, warnings=result.guardrail_warnings)
                        if accepted is not None:
                            submission = accepted
                            finished = True
                        else:
                            revisions += 1
                    elif tool_calls >= settings.max_tool_calls:
                        output = {"ok": False, "error": {"code": "tool_budget_exhausted",
                                                         "message": f"Tool budget reached. Call {SUBMIT_TOOL} now."}}
                    else:
                        tool_calls += 1
                        output, duration = self.registry.timed_execute(call.name, call.arguments, ctx)
                        record = self._record(len(result.tool_calls) + 1, call, output, duration)
                        result.tool_calls.append(record)
                        if output.get("ok"):
                            evidence[output["evidence_id"]] = output
                        log(trace_id, "tool_call", step=record.step, tool=call.name, arguments=record.arguments,
                            ok=record.ok, evidence_id=record.evidence_id, error_code=record.error_code,
                            duration_ms=duration, summary=record.summary)
                    input_items.append({"type": "function_call_output", "call_id": call.call_id,
                                        "output": json.dumps(output, default=str)})
                if finished:
                    break
        except Exception as exc:  # API errors, network failures, etc.
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            log(trace_id, "run_failed", error=result.error)
        finally:
            con.close()

        if submission is not None:
            result.status = "completed"
            result.headline = submission["headline"]
            result.answer = submission["answer"]
            result.findings = [Finding(f["statement"], f["kind"], list(f.get("evidence_ids") or []),
                                       f.get("how_to_verify") or "") for f in submission["findings"]]
            result.data_gaps = list(submission["data_gaps"])
            result.recommendations = [Recommendation(r["action"], r["rationale"], list(r.get("evidence_ids") or []))
                                      for r in submission["recommendations"]]
        log(trace_id, "run_finished", status=result.status, model_calls=result.model_calls,
            tool_calls=len(result.tool_calls), guardrail_warnings=len(result.guardrail_warnings))
        self._save_run(result)
        return result

    # ------------------------------------------------------------- helpers
    def _review_submission(self, arguments: str, evidence: dict[str, dict[str, Any]], revisions: int,
                           result: AnalysisResult) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            submission = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": {"code": "invalid_json", "message": str(exc)}}, None
        warnings = check_submission(submission, evidence)
        if warnings and revisions < MAX_REVISIONS:
            return {"ok": False, "error": {
                "code": "guardrail_revision_requested",
                "message": ("Fix these issues and call submit_analysis again. Use only numbers returned by tools, "
                            "cite valid evidence IDs, and label causes as hypotheses:\n- " + "\n- ".join(warnings)),
            }}, None
        result.guardrail_warnings = warnings
        return {"ok": True, "message": "Analysis received."}, submission

    @staticmethod
    def _record(step: int, call: Any, output: dict[str, Any], duration: int) -> ToolCallRecord:
        try:
            args = json.loads(call.arguments) if isinstance(call.arguments, str) else dict(call.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {"_raw": str(call.arguments)[:500]}
        return ToolCallRecord(step=step, evidence_id=output.get("evidence_id", "-"), tool=call.name, arguments=args,
                              ok=bool(output.get("ok")), duration_ms=duration, summary=summarize_result(output),
                              error_code=(output.get("error") or {}).get("code"), output=output)

    def _save_run(self, result: AnalysisResult) -> None:
        folder = self.settings.logs_dir / "runs"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{result.trace_id}.json").write_text(
                json.dumps(result.as_dict(include_outputs=True), indent=2, default=str), encoding="utf-8")
        except OSError:
            pass
