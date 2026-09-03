from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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
    trace.configure_theorem_stages(iterations=2, checkpoint_every=1, has_literature_agent=True)
    trace.log(
        "agent_start",
        agent="Theorist",
        model="fake/model",
        reasoning_effort="high",
        step_key="iter:1:proposer",
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
    assert stages[0]["step_key"] == "iter:1:proposer"
    assert stages[0]["label"] == "Tur 1 · Theorist · öneri"
    assert stages[0]["total"] == 12
    assert stages[0]["total_is_minimum"] is True
    assert ends[0]["total"] == 12
    assert ends[0]["total_is_minimum"] is True
    assert ends[0]["total_tokens"] == 18
    assert ends[0]["reasoning_tokens"] == 5


@pytest.mark.parametrize(
    ("step_key", "agent", "expected"),
    [
        ("literature:agent", "LiteratureScout", "Literatür · LiteratureScout"),
        ("iter:2:proposer", "Theorist", "Tur 2 · Theorist · öneri"),
        (
            "iter:2:verifier",
            "VerificationEngineer",
            "Tur 2 · VerificationEngineer · doğrulama",
        ),
        (
            "iter:2:critic",
            "AdversarialCritic",
            "Tur 2 · AdversarialCritic · eleştiri",
        ),
        ("iter:2:manager", "ResearchManager", "Tur 2 · ResearchManager · karar"),
        (
            "iter:2:tool:plan:3",
            "CodeExperimentAgent",
            "Tur 2 · CodeExperimentAgent · adım 3",
        ),
        ("iter:2:checkpoint_audit", "IndependentAuditor", "Tur 2 · Denetim"),
        ("final:audit", "IndependentAuditor", "Final denetim"),
        (
            "iter:2:manager:json_repair",
            "ResearchManager",
            "Tur 2 · ResearchManager · karar · JSON onarımı",
        ),
    ],
)
def test_theorem_stage_label_map(step_key, agent, expected):
    assert Trace._theorem_stage_label(step_key, agent) == expected


def test_theorem_stage_total_formula_two_configurations(tmp_path):
    first = Trace("theorem-total-a", out_dir=tmp_path / "runs")
    assert first.configure_theorem_stages(
        iterations=3,
        checkpoint_every=2,
        has_literature_agent=True,
    ) == 15
    first.close()

    second = Trace("theorem-total-b", out_dir=tmp_path / "runs")
    assert second.configure_theorem_stages(
        iterations=4,
        checkpoint_every=0,
        has_literature_agent=False,
    ) == 17
    second.close()


def test_theorem_stage_index_honors_resume_offset(tmp_path):
    trace = Trace("theorem-offset", out_dir=tmp_path / "runs")
    trace.log(
        "project_context",
        project_id="p",
        project_uuid="u",
        experiment="Teorem Araştırması",
        experiment_method="theorem_lab",
    )
    trace.configure_theorem_stages(iterations=6, checkpoint_every=2, has_literature_agent=True)
    trace._stage_index = 10
    trace.log(
        "agent_start",
        agent="Theorist",
        model="fake/model",
        reasoning_effort="high",
        step_key="iter:3:proposer",
    )
    rows = _rows(trace)
    trace.close()

    stage = next(row for row in rows if row.get("type") == "stage")
    assert stage["index"] == 11
    assert stage["total"] == 29


def test_unknown_theorem_step_key_keeps_legacy_label():
    assert Trace._theorem_stage_label("iter:1:proposal", "Theorist") == (
        "Theorist · iter:1:proposal"
    )


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
