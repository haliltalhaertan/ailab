from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab.orchestrator import Orchestrator
from lab.run_controller import ResearchStopped
from lab.trace import Trace


class FakeAgent:
    def __init__(self, name: str, *, stream: bool = False):
        self.name = name
        self.model = "fake/model"
        self.temperature = 0.2
        self.reasoning_effort = "high"
        self.system_prompt = f"System for {name}"
        self.stream = stream
        self.calls = 0

    def respond(self, messages: list[dict], stream_callback=None):
        self.calls += 1
        if self.stream and stream_callback is not None:
            stream_callback("reasoning", "first reasoning chunk")
            stream_callback("content", "second content chunk")
        content = f"{self.name} answer {self.calls}"
        response = SimpleNamespace(
            content=content,
            model=self.model,
            request_messages=messages,
            requested_reasoning_effort=self.reasoning_effort,
            provider_reasoning="",
            reasoning_details=None,
            prompt_tokens=20,
            completion_tokens=10,
            reasoning_tokens=7,
            cached_tokens=0,
            cost_usd=0.002,
            latency_s=1.25,
        )
        return content, response


def _events(trace: Trace) -> list[dict]:
    trace._flush_stream(force=True)
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("method", "expected_count", "runner", "expected_labels"),
    [
        (
            "research_loop",
            6,
            lambda orch: orch.research_loop(
                "problem",
                FakeAgent("Teorisyen"),
                FakeAgent("Sceptik"),
                iterations=2,
                synthesizer=FakeAgent("Raporcu"),
            ),
            [
                "İlk çözüm · Teorisyen",
                "Tur 1/2 · Sceptik · eleştiri",
                "Tur 1/2 · Teorisyen · revizyon",
                "Tur 2/2 · Sceptik · eleştiri",
                "Tur 2/2 · Teorisyen · revizyon",
                "Rapor · Raporcu",
            ],
        ),
        (
            "debate",
            5,
            lambda orch: orch.debate(
                "topic",
                [FakeAgent("A"), FakeAgent("B")],
                rounds=2,
                judge=FakeAgent("Hakem"),
            ),
            ["Tur 1/2 · A", "Tur 1/2 · B", "Tur 2/2 · A", "Tur 2/2 · B", "Hakem"],
        ),
        (
            "pipeline",
            3,
            lambda orch: orch.pipeline(
                "task",
                [FakeAgent("Araştırmacı"), FakeAgent("Analist"), FakeAgent("Eleştirmen")],
            ),
            ["Adım 1/3 · Araştırmacı", "Adım 2/3 · Analist", "Adım 3/3 · Eleştirmen"],
        ),
        (
            "panel",
            4,
            lambda orch: orch.panel(
                "question",
                [FakeAgent("P1"), FakeAgent("P2"), FakeAgent("P3")],
                synthesizer=FakeAgent("Sentezleyici"),
            ),
            ["Panelist 1/3", "Panelist 2/3", "Panelist 3/3", "Sentez · Sentezleyici"],
        ),
    ],
)
def test_orchestrator_emits_stage_pairs_and_exact_totals(
    tmp_path: Path,
    method,
    expected_count,
    runner,
    expected_labels,
):
    trace = Trace(f"test-{method}", out_dir=tmp_path / "runs")
    callback_events: list[dict] = []
    orch = Orchestrator(trace, on_stage=callback_events.append)
    runner(orch)
    events = _events(trace)
    trace.close()

    starts = [event for event in events if event.get("type") == "stage"]
    ends = [event for event in events if event.get("type") == "stage_end"]
    assert len(starts) == expected_count
    assert len(ends) == expected_count
    assert [event["label"] for event in starts] == expected_labels
    assert all(event["method"] == method for event in starts)
    assert all(event["total"] == expected_count for event in starts)
    assert all(event["total_tokens"] == 30 for event in ends)
    assert all(event["reasoning_tokens"] == 7 for event in ends)
    assert all(event.get("ts") for event in callback_events)


def test_cancel_before_second_llm_call_stops_without_calling_next_agent(tmp_path: Path):
    trace = Trace("cancel-before", out_dir=tmp_path / "runs")
    proposer = FakeAgent("Teorisyen")
    critic = FakeAgent("Sceptik")
    checks = 0

    def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    orch = Orchestrator(trace, cancel_check=cancel_check)
    with pytest.raises(ResearchStopped):
        orch.research_loop("problem", proposer, critic, iterations=1)
    trace.close()
    assert proposer.calls == 1
    assert critic.calls == 0


def test_stream_callback_can_stop_long_reasoning_mid_call(tmp_path: Path):
    trace = Trace("cancel-stream", out_dir=tmp_path / "runs")
    streaming = FakeAgent("Araştırmacı", stream=True)
    checks = 0

    def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    orch = Orchestrator(trace, cancel_check=cancel_check)
    with pytest.raises(ResearchStopped):
        orch.pipeline("task", [streaming])
    trace.close()
    assert streaming.calls == 1
    stream_rows = [
        json.loads(line)
        for line in trace.stream_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row.get("channel") == "reasoning" for row in stream_rows)
