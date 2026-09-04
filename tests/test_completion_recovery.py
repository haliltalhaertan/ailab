from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab import TheoremResearchLab
from lab.agent import Agent
from lab.client import LLMResponse
from lab.prompts import proposal_prompt
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused
from lab.trace import Trace


class SequenceClient:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected provider call")
        response = self.responses.pop(0)
        response.request_messages = list(messages)
        return response


def _response(content: str, finish_reason: str, *, effort: str = "high", cost: float = 0.01) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/model",
        prompt_tokens=10,
        completion_tokens=20,
        reasoning_tokens=15,
        latency_s=1.0,
        cost_usd=cost,
        finish_reason=finish_reason,
        requested_max_tokens=100000,
        model_max_completion_tokens=100000,
        max_tokens_source="catalog",
        catalog_source="memory",
        requested_reasoning_effort=effort,
        reasoning_effort_sent=effort,
        effort_resolution="exact",
    )


def _agent(client: SequenceClient, *, effort: str = "high") -> Agent:
    return Agent(
        name="Theorist",
        system_prompt="system",
        model="test/model",
        temperature=0.0,
        max_tokens=None,
        reasoning_effort=effort,
        client=client,  # type: ignore[arg-type]
    )


def _lab(tmp_path: Path, name: str = "recovery") -> tuple[TheoremResearchLab, Trace]:
    state = ResearchState(tmp_path / "state")
    trace = Trace(name, out_dir=tmp_path / "runs")
    return TheoremResearchLab(trace, state), trace


def test_length_empty_retries_once_and_preserves_original_agent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lab.theorem_engine.next_lower_supported_effort", lambda model, effort: "medium")
    client = SequenceClient(
        [
            _response("", "length", effort="high", cost=0.10),
            _response('{"title":"T","claim":"C"}', "stop", effort="medium", cost=0.02),
        ]
    )
    agent = _agent(client, effort="high")
    lab, trace = _lab(tmp_path)

    result = lab._call_json(agent, "return json", "iter:1:proposer")
    trace.close()

    assert result == {"title": "T", "claim": "C"}
    assert len(client.calls) == 2
    assert agent.reasoning_effort == "high"
    assert client.calls[1]["reasoning_effort"] == "medium"
    assert "provider token limit" in client.calls[1]["messages"][-1]["content"]
    first_cache = lab._cache_get("iter:1:proposer")
    retry_cache = lab._cache_get("iter:1:proposer:truncated_retry")
    assert first_cache and first_cache["status"] == "TRUNCATED_EMPTY"
    assert retry_cache and retry_cache["status"] == "COMPLETE"
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    retry_event = next(event for event in events if event.get("type") == "truncated_retry")
    assert retry_event["outcome"] == "recovered"
    assert retry_event["first"]["cost_usd"] == 0.10
    assert retry_event["retry"]["cost_usd"] == 0.02


def test_truncated_nonempty_prefix_keeps_pr_c_path_without_retry(tmp_path: Path):
    client = SequenceClient(
        [_response('{"title":"Cut","claim":"complete","strategy":"ok","tool_request":{"tool":"z3"', "length")]
    )
    agent = _agent(client)
    lab, trace = _lab(tmp_path)

    result = lab._call_json(agent, "return json", "iter:1:proposer")
    trace.close()

    assert result == {"title": "Cut", "claim": "complete", "strategy": "ok"}
    assert len(client.calls) == 1
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("type") == "truncated_retry" for event in events)


def test_second_length_empty_exhausts_retry_and_resume_makes_zero_calls(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lab.theorem_engine.next_lower_supported_effort", lambda model, effort: effort)
    client = SequenceClient([_response("", "length"), _response("", "length")])
    agent = _agent(client)
    lab, trace = _lab(tmp_path, "first")

    with pytest.raises(ResearchPaused, match="automatic truncated retry already exhausted"):
        lab._call_json(agent, "return json", "iter:1:proposer")
    trace.close()
    assert len(client.calls) == 2

    resumed_client = SequenceClient([])
    resumed_agent = _agent(resumed_client)
    resumed_trace = Trace("resume", out_dir=tmp_path / "runs")
    resumed = TheoremResearchLab(resumed_trace, ResearchState(tmp_path / "state"))
    with pytest.raises(ResearchPaused, match="automatic truncated retry already exhausted"):
        resumed._call_json(resumed_agent, "return json", "iter:1:proposer")
    resumed_trace.close()
    assert resumed_client.calls == []


def test_legacy_complete_length_empty_skips_original_paid_call_even_after_prompt_fingerprint_change(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lab.theorem_engine.next_lower_supported_effort", lambda model, effort: effort)
    client = SequenceClient([_response('{"title":"Recovered","claim":"C"}', "stop")])
    agent = _agent(client)
    lab, trace = _lab(tmp_path, "legacy")
    lab._cache_put(
        "iter:1:proposer",
        {
            "status": "COMPLETE",
            "fingerprint": "old-pr-c-fingerprint",
            "content": "",
            "model": "test/model",
            "reasoning_effort": "high",
            "finish_reason": "length",
            "truncated": True,
            "requested_max_tokens": None,
            "completed_at": "legacy",
        },
    )

    result = lab._call_json(agent, "new PR-E prompt", "iter:1:proposer")
    trace.close()

    assert result["title"] == "Recovered"
    assert len(client.calls) == 1
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    reused = next(event for event in events if event.get("type") == "truncated_empty_reused")
    assert reused["legacy"] is True
    assert reused["fingerprint_match"] is False


def test_normal_empty_stop_uses_existing_json_repair_not_truncation_retry(tmp_path: Path):
    client = SequenceClient([_response("", "stop"), _response('{"title":"Fixed","claim":"C"}', "stop")])
    agent = _agent(client)
    lab, trace = _lab(tmp_path, "repair")

    result = lab._call_json(agent, "return json", "iter:1:proposer")
    trace.close()

    assert result["title"] == "Fixed"
    assert len(client.calls) == 2
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("type") == "truncated_retry" for event in events)
    assert any(event.get("type") == "structured_output_repaired" for event in events)


def test_theorist_prompt_delegates_mechanical_computation_and_hides_absent_contract():
    without_contract = proposal_prompt("P", "L", "ledger", "task")
    assert "Numerical trajectories" in without_contract
    assert "delegate it to script/code_experiment" in without_contract
    assert "OPEN TARGETS" not in without_contract

    with_contract = proposal_prompt(
        "P",
        "L",
        "ledger",
        "task",
        contract_block="OPEN TARGETS: T1",
    )
    assert "target_id MUST be one of the OPEN TARGETS" in with_contract
