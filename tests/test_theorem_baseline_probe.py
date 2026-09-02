from __future__ import annotations

import json
from pathlib import Path

from lab.baseline_probe import _markdown_report, summarize_probe


def test_summarize_probe_counts_run_level_events(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    events = [
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


def test_markdown_report_exposes_acceptance_metrics() -> None:
    report = {
        "git_sha": "abc123",
        "run_id": "run-1",
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
        "tool_results": [
            {"step_key": "iter:1:tool", "tool": "z3", "ok": True, "error": "", "metadata": {}}
        ],
        "llm_calls": [
            {
                "agent": "Theorist",
                "model": "example/model",
                "total_tokens": 300,
                "cost_usd": 0.02,
                "latency_s": 1.0,
            }
        ],
    }

    text = _markdown_report(report)

    assert "JSON repairs completed: **1**" in text
    assert "guard downgrades: **1**" in text
    assert "iteration 1: `OPEN` / `REVISE`" in text
    assert "`iter:1:tool`: `z3`" in text
