from __future__ import annotations

import json
from types import SimpleNamespace

from lab.trace import Trace


def _rows(trace: Trace) -> list[dict]:
    trace._flush_stream(force=True)
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]


def test_theorem_style_agent_events_get_common_stage_pair(tmp_path):
    trace = Trace("theorem-stage", out_dir=tmp_path / "runs")
    trace.log(
        "project_context",
        project_id="p",
        project_uuid="u",
        experiment="Teorem Araştırması",
        experiment_method="theorem_lab",
    )
    trace.log(
        "agent_start",
        agent="Theorist",
        model="fake/model",
        reasoning_effort="high",
        step_key="iter:1:proposal",
    )
    response = SimpleNamespace(
        content="answer",
        request_messages=[{"role": "user", "content": "problem"}],
        requested_reasoning_effort="high",
        provider_reasoning="reasoning",
        reasoning_details=None,
        prompt_tokens=11,
        completion_tokens=7,
        reasoning_tokens=5,
        cached_tokens=0,
        cost_usd=0.002,
        latency_s=2.5,
    )
    trace.agent_call("Theorist", "fake/model", 0.2, response.request_messages, response)
    rows = _rows(trace)
    trace.close()

    stages = [row for row in rows if row.get("type") == "stage"]
    ends = [row for row in rows if row.get("type") == "stage_end"]
    assert len(stages) == 1
    assert len(ends) == 1
    assert stages[0]["method"] == "theorem_lab"
    assert stages[0]["step_key"] == "iter:1:proposal"
    assert stages[0]["total"] is None
    assert ends[0]["total_tokens"] == 18
    assert ends[0]["reasoning_tokens"] == 5


def test_explicit_orchestrator_stage_is_not_duplicated(tmp_path):
    trace = Trace("explicit-stage", out_dir=tmp_path / "runs")
    trace.log(
        "stage",
        method="pipeline",
        label="Adım 1/1 · A",
        index=1,
        total=1,
        agent="A",
        model="fake/model",
        reasoning_effort="medium",
        step_key="pipeline:1",
    )
    trace.log(
        "agent_start",
        agent="A",
        model="fake/model",
        reasoning_effort="medium",
        step_key="pipeline:1",
    )
    rows = _rows(trace)
    trace.close()
    assert len([row for row in rows if row.get("type") == "stage"]) == 1
