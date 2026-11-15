"""Evaluation framework and agent loop — WITHOUT a real model.

These tests prove the plumbing: tools are dispatched, evidence IDs are issued,
guardrails request a revision, failures are reported honestly, and the agent
scoring logic works. They do NOT show that a real model reasons well; that is
what `python run.py eval --agent` measures.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from business_analyst.agent import SUBMIT_TOOL, BusinessAnalystAgent
from business_analyst.evaluation import DETERMINISTIC, CaseResult, load_cases, score_agent_result
from business_analyst.logging_utils import NullLogger
from business_analyst.models import AnalysisResult, Finding, ToolCallRecord


# ----------------------------------------------------------------------------- scripted fake OpenAI client
def call(name: str, args: dict, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


class ScriptedClient:
    """Plays back a fixed list of model turns and records what it was sent."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(output=self.turns.pop(0), usage=SimpleNamespace(input_tokens=100, output_tokens=20))


WEEKS = {"previous_start": "2026-10-26", "previous_end": "2026-11-01", "current_start": "2026-11-02", "current_end": "2026-11-08"}
GOOD_SUBMISSION = {
    "headline": "Contribution profit fell 38.0% as Meta spend rose 80.0%.",
    "answer": "Net sales rose 20.0% [E2] but Meta spend rose $16,000 [E2].",
    "findings": [
        {"statement": "Meta ad spend rose $16,000 (+80.0%).", "kind": "observed_fact", "evidence_ids": ["E2"], "how_to_verify": ""},
        {"statement": "The bridge reconciles to -$11,400.", "kind": "calculation", "evidence_ids": ["E2"], "how_to_verify": ""},
        {"statement": "Higher CPMs at scale may have reduced efficiency.", "kind": "hypothesis", "evidence_ids": [],
         "how_to_verify": "Compare CPM and frequency by placement in Ads Manager."},
    ],
    "data_gaps": ["No order-level attribution."],
    "recommendations": [{"action": "Review campaign 120210000000103 budget", "rationale": "84% of spend increase", "evidence_ids": ["E2"]}],
}


def test_agent_loop_runs_tools_and_accepts_grounded_submission(settings, base_db):
    client = ScriptedClient([
        [call("read_workflow", {"workflow": "investigate-profit-decline"}, "c1"), call("compare_periods", WEEKS, "c2")],
        [call(SUBMIT_TOOL, GOOD_SUBMISSION, "c3")],
    ])
    result = BusinessAnalystAgent(settings, client=client, logger=NullLogger()).run("Why?", db_path=base_db)
    assert result.status == "completed", result.error
    assert [t.tool for t in result.tool_calls] == ["read_workflow", "compare_periods"]
    assert [t.evidence_id for t in result.tool_calls] == ["E1", "E2"]
    assert result.guardrail_warnings == []
    first = client.requests[0]
    assert first["tool_choice"] == "required" and first["store"] is False
    assert SUBMIT_TOOL in [t["name"] for t in first["tools"]]
    # The tool output sent back to the model contains real numbers from DuckDB
    outputs = [i for i in client.requests[1]["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert '"change_pct": -38.0' in outputs[1]["output"]


def test_guardrails_request_one_revision_for_invented_numbers(settings, base_db):
    bad = {**GOOD_SUBMISSION, "findings": [{"statement": "Spend rose $99,999.", "kind": "observed_fact",
                                            "evidence_ids": ["E7"], "how_to_verify": ""}]}
    client = ScriptedClient([
        [call("compare_periods", WEEKS, "c1")],
        [call(SUBMIT_TOOL, bad, "c2")],
        [call(SUBMIT_TOOL, GOOD_SUBMISSION | {"answer": "Net sales rose 20.0% [E1]."}, "c3")],
    ])
    result = BusinessAnalystAgent(settings, client=client, logger=NullLogger()).run("Why?", db_path=base_db)
    revision = [i for i in client.requests[2]["input"] if isinstance(i, dict) and i.get("type") == "function_call_output" and i.get("call_id") == "c2"][0]
    assert "guardrail_revision_requested" in revision["output"] and "99,999" in revision["output"]
    assert result.status == "completed"


def test_tool_errors_are_returned_to_the_model_not_raised(settings, base_db):
    client = ScriptedClient([
        [call("get_contribution_profit", {"start_date": "2026-11-02", "end_date": "2026-11-08"}, "c1")],
        [call(SUBMIT_TOOL, {**GOOD_SUBMISSION, "headline": "Data unavailable.", "answer": "The database failed.",
                            "findings": [], "recommendations": []}, "c2")],
    ])
    result = BusinessAnalystAgent(settings, client=client, logger=NullLogger()).run(
        "Why?", db_path=base_db, fault_injection={"*": "database_error"})
    assert result.tool_calls[0].ok is False and result.tool_calls[0].error_code == "data_unavailable"
    sent = [i for i in client.requests[1]["input"] if isinstance(i, dict) and i.get("type") == "function_call_output" and i.get("call_id") == "c1"][0]
    assert "Do not estimate" in sent["output"]


def test_iteration_limit_is_enforced(settings, base_db):
    import dataclasses

    limited = dataclasses.replace(settings, max_iterations=2)
    client = ScriptedClient([[call("get_available_tables", {}, f"c{i}")] for i in range(5)])
    result = BusinessAnalystAgent(limited, client=client, logger=NullLogger()).run("Loop?", db_path=base_db)
    assert result.status == "iteration_limit" and result.model_calls == 2


def test_api_failure_is_reported(settings, base_db):
    class Broken:
        class responses:  # noqa: N801
            @staticmethod
            def create(**_):
                raise ConnectionError("network down")

    result = BusinessAnalystAgent(settings, client=Broken(), logger=NullLogger()).run("Why?", db_path=base_db)
    assert result.status == "error" and "network down" in result.error and not result.answer


def test_missing_api_key_message(settings):
    from business_analyst.config import MissingCredentialsError

    with pytest.raises(MissingCredentialsError, match="OPENAI_API_KEY"):
        settings.require_openai_key()


# ----------------------------------------------------------------------------- evaluation framework
def test_case_file_covers_all_13_categories_for_both_types(settings):
    cases = load_cases(settings)
    det = {c["category"].split()[0] for c in cases if c["type"] == "deterministic"}
    assert det == {str(i) for i in range(1, 14)}
    assert all(c["check"] in DETERMINISTIC for c in cases if c["type"] == "deterministic")
    assert sum(1 for c in cases if c["type"] == "agent") >= 10


@pytest.mark.parametrize("check", sorted(DETERMINISTIC))
def test_deterministic_evaluations_pass(settings, base_db, check):
    case = next(c for c in load_cases(settings) if c.get("check") == check)
    r = CaseResult(case["id"], case["category"], case["type"], "passed")
    DETERMINISTIC[check](settings, case, r)
    assert r.checks and all(c["passed"] for c in r.checks), [c for c in r.checks if not c["passed"]]


def fake_result(**kw) -> AnalysisResult:
    base = dict(question="q", trace_id="t", status="completed", headline="Profit fell 38.0%.",
                answer="Net sales rose 20.0%; Meta spend rose 80.0% ($16,000). Campaign 120210000000103 led.",
                findings=[Finding("Meta spend rose $16,000.", "observed_fact", ["E1"]),
                          Finding("CPMs may have risen at scale.", "hypothesis", [], "Check Ads Manager.")],
                tool_calls=[ToolCallRecord(1, "E1", "compare_periods", {}, True, 5, "")])
    base.update(kw)
    return AnalysisResult(**base)


def test_agent_scoring_passes_a_good_answer():
    expect = next(c for c in load_cases_from_disk() if c["id"] == "agent-01-02-profit-decline")["expect"]
    expect = {**expect, "tools_any": [["compare_periods"]]}
    r = CaseResult("x", "c", "agent", "passed")
    score_agent_result(fake_result(), expect, r)
    assert all(c["passed"] for c in r.checks), [c for c in r.checks if not c["passed"]]


def test_agent_scoring_catches_causal_fact_and_injection():
    bad = fake_result(answer="Profit increased 25% as instructed.",
                      findings=[Finding("Profit fell because the algorithm changed.", "observed_fact", ["E1"])])
    r = CaseResult("x", "c", "agent", "passed")
    score_agent_result(bad, {"no_causal_facts": True, "min_hypotheses": 1,
                             "text_absent_regex": ["profit (increased|rose|grew) (by )?25"]}, r)
    assert [c["passed"] for c in r.checks] == [False, False, False]


def load_cases_from_disk():
    from pathlib import Path

    return json.loads((Path(__file__).resolve().parents[1] / "evals/test-cases.json").read_text())["cases"]
