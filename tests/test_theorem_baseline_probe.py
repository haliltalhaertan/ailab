from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lab import baseline_probe
from lab.agent import Agent
from lab.baseline_probe import _markdown_report, resolve_agent_config, summarize_probe
from lab.client import LLMResponse


def test_summarize_probe_counts_run_level_events(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    events = [
        {"type": "baseline_probe_config", "iterations": 2, "agent_config": {"Theorist": {"model": "m"}}},
        {"type": "structured_output_parse_failed"},
        {"type": "structured_output_repaired"},
        {
            "type": "status_downgraded_by_guard",
            "iteration": 1,
            "requested": "PROVEN",
            "granted": "OPEN",
        },
        {
            "type": "tool_result",
            "step_key": "iter:1:tool",
            "tool": "z3",
            "ok": True,
            "error": "",
            "metadata": {"status": "unsat"},
        },
        {
            "type": "stage",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
            "label": "Tur 1 · Theorist · öneri",
        },
        {
            "type": "llm_call",
            "agent": "Theorist",
            "model": "example/model",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": 10,
            "cached_tokens": 0,
            "total_tokens": 150,
            "cost_usd": 0.001,
            "latency_s": 1.2,
            "output": "candidate output",
        },
        {
            "type": "stage_end",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
        },
        {
            "type": "iteration_end",
            "iteration": 1,
            "item_id": "C1",
            "decision": "REVISE",
            "status": "OPEN",
            "next_task": "check again",
        },
    ]
    trace_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "finished_at": "2026-09-02T00:00:00+00:00",
                "wall_time_s": 3.5,
                "total_calls": 1,
                "total_prompt_tokens": 100,
                "total_completion_tokens": 50,
                "total_reasoning_tokens": 10,
                "total_cached_tokens": 0,
                "total_tokens": 150,
                "total_cost_usd": 0.001,
                "cost_complete": True,
                "agents": {},
            }
        ),
        encoding="utf-8",
    )

    report = summarize_probe(trace_path, summary_path)

    assert report["event_counts"]["structured_output_parse_failed"] == 1
    assert report["event_counts"]["structured_output_repaired"] == 1
    assert report["event_counts"]["status_downgraded_by_guard"] == 1
    assert report["requested_iterations"] == 2
    assert report["completed_iterations"] == 1
    assert report["iterations"] == [
        {
            "iteration": 1,
            "item_id": "C1",
            "decision": "REVISE",
            "status": "OPEN",
            "next_task": "check again",
        }
    ]
    assert report["tool_results"][0]["tool"] == "z3"
    assert report["llm_calls"][0]["total_tokens"] == 150
    assert report["llm_calls"][0]["step_key"] == "iter:1:proposer"
    assert report["per_iteration"][0]["total_tokens"] == 150
    assert report["per_iteration"][0]["cost_usd"] == 0.001
    assert report["role_outputs"]["Theorist"][0]["output_preview"] == "candidate output"


def test_resolve_agent_config_accepts_worker_request_agent_dictionary() -> None:
    resolved = resolve_agent_config(
        model="fallback/model",
        reasoning_effort="low",
        max_tokens=1200,
        agent_config={
            "agents": {
                "Theorist": {
                    "model": "deepseek/deepseek-v4-pro",
                    "reasoning_effort": "high",
                    "max_tokens": 1600,
                },
                "AdversarialCritic": {
                    "model": "moonshotai/kimi-k2.5",
                    "reasoning_effort": "high",
                },
            }
        },
    )

    assert resolved["Theorist"] == {
        "model": "deepseek/deepseek-v4-pro",
        "reasoning_effort": "high",
        "max_tokens": 1600,
    }
    assert resolved["AdversarialCritic"]["model"] == "moonshotai/kimi-k2.5"
    assert resolved["ResearchManager"] == {
        "model": "fallback/model",
        "reasoning_effort": "low",
        "max_tokens": 1200,
    }


def test_markdown_report_exposes_acceptance_metrics() -> None:
    report = {
        "git_sha": "abc123",
        "run_id": "run-1",
        "requested_iterations": 2,
        "completed_iterations": 1,
        "agent_config": {
            "Theorist": {
                "model": "example/model",
                "reasoning_effort": "high",
                "max_tokens": 1200,
            }
        },
        "total_calls": 2,
        "total_tokens": 300,
        "total_cost_usd": 0.02,
        "wall_time_s": 4.0,
        "event_counts": {
            "structured_output_parse_failed": 1,
            "structured_output_repaired": 1,
            "structured_output_repair_failed": 0,
            "status_downgraded_by_guard": 1,
            "tool_result": 1,
            "agent_error": 0,
            "agent_retry": 0,
            "step_reused": 0,
        },
        "iterations": [
            {"iteration": 1, "status": "OPEN", "decision": "REVISE", "item_id": "C1", "next_task": "x"}
        ],
        "per_iteration": [
            {
                "iteration": 1,
                "calls": 1,
                "total_tokens": 300,
                "cost_usd": 0.02,
                "cost_available_calls": 1,
                "cost_complete": True,
                "llm_latency_s": 1.0,
                "roles": ["Theorist"],
            }
        ],
        "role_outputs": {
            "Theorist": [
                {"step_key": "iter:1:proposer", "model": "example/model", "output_preview": "candidate"}
            ]
        },
        "errors": [],
        "tool_results": [
            {"step_key": "iter:1:tool", "tool": "z3", "ok": True, "error": "", "metadata": {}}
        ],
        "llm_calls": [
            {
                "agent": "Theorist",
                "model": "example/model",
                "step_key": "iter:1:proposer",
                "completion_tokens": 200,
                "reasoning_tokens": 150,
                "answer_chars": 900,
                "reasoning_completion_ratio": 0.75,
                "total_tokens": 300,
                "cost_usd": 0.02,
                "latency_s": 1.0,
            }
        ],
    }

    text = _markdown_report(report)

    assert "requested/completed iterations: **2/1**" in text
    assert "JSON repairs completed: **1**" in text
    assert "guard downgrades: **1**" in text
    assert "iteration 1: **300 tokens**, **$0.020000**" in text
    assert "iteration 1: `OPEN` / `REVISE`" in text
    assert "**Theorist**" in text
    assert "`iter:1:proposer`: candidate" in text
    assert "`iter:1:tool`: `z3`" in text
    assert "## Errors\n- none recorded" in text
    assert "completion=200, reasoning=150, answer_chars=900" in text
    assert "ratio=0.7500" in text


class _FakeClient:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream_callback: Any = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        del messages, temperature, max_tokens, reasoning_effort
        if not self.outputs:
            raise AssertionError("fake client output queue exhausted")
        content = self.outputs.pop(0)
        if stream_callback is not None:
            stream_callback("content", content)
        return LLMResponse(
            content=content,
            model=model or "fake/model",
            prompt_tokens=1,
            completion_tokens=1,
            latency_s=0.0,
            cost_usd=0.0,
        )


def test_run_probe_exercises_real_engine_with_fake_agents(tmp_path: Path, monkeypatch: Any) -> None:
    outputs = {
        "ResearchManager": [
            json.dumps(
                {
                    "decision": "REVISE",
                    "status": "OPEN",
                    "reason": "baseline regression test",
                    "next_task": "done",
                }
            )
        ],
        "Theorist": [
            json.dumps(
                {
                    "title": "Toy parity claim",
                    "claim": "For 0 <= n <= 20, n*(n+1) is even.",
                    "tool_request": {"tool": "none"},
                }
            )
        ],
        "AdversarialCritic": [
            json.dumps({"verdict": "OPEN", "counterexample": "", "reason": "no counterexample supplied"})
        ],
        "VerificationEngineer": [
            json.dumps({"status": "OPEN", "counterexample": "", "reason": "no deterministic evidence requested"})
        ],
        "LiteratureScout": ["No literature lookup in fake baseline."],
        "IndependentAuditor": ["Fake final audit completed."],
    }

    seen: dict[str, tuple[str, str | None]] = {}

    def fake_agent(role: str, model: str, max_tokens: int, reasoning_effort: str | None) -> Agent:
        del max_tokens
        seen[role] = (model, reasoning_effort)
        return Agent(
            name=role,
            system_prompt=f"fake {role}",
            model=model,
            temperature=0.0,
            max_tokens=128,
            reasoning_effort=reasoning_effort,
            client=_FakeClient(outputs[role]),  # type: ignore[arg-type]
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    monkeypatch.setattr(baseline_probe, "_git_sha", lambda: "test-sha")
    monkeypatch.setattr(baseline_probe, "_agent", fake_agent)

    report_path = baseline_probe.run_probe(
        model="fake/model",
        iterations=1,
        max_tokens=128,
        reasoning_effort="low",
        out_dir=tmp_path / "baseline_runs",
        problem="Toy baseline regression problem",
        agent_config={
            "agents": {
                "Theorist": {"model": "fake/theorist", "reasoning_effort": "high"},
                "VerificationEngineer": {"model": "fake/verifier", "reasoning_effort": "medium"},
            }
        },
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["run_error"] == ""
    assert report["git_sha"] == "test-sha"
    assert report["iterations"] == [
        {
            "iteration": 1,
            "item_id": report["iterations"][0]["item_id"],
            "decision": "REVISE",
            "status": "OPEN",
            "next_task": "done",
        }
    ]
    assert report["total_calls"] == 6
    assert report["event_counts"]["structured_output_repair_failed"] == 0
    assert seen["Theorist"] == ("fake/theorist", "high")
    assert seen["VerificationEngineer"] == ("fake/verifier", "medium")
    assert seen["ResearchManager"] == ("fake/model", "low")
    assert report["agent_config"]["Theorist"]["model"] == "fake/theorist"
