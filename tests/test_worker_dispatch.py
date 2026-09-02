from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import lab.worker as worker
from lab.integrity import ProjectBusyError, ProjectRunLock
from lab.project_manager import ProjectManager
from lab.worker_launcher import launch_worker, write_worker_request


class FakeAgent:
    def __init__(self, name: str, raw: dict):
        self.name = str(raw.get("display_role") or raw.get("name") or name)
        self.model = str(raw.get("model") or "fake/model")
        self.temperature = float(raw.get("temperature", raw.get("temp", 0.2)))
        self.reasoning_effort = raw.get("reasoning_effort")
        self.calls = 0

    def respond(self, messages: list[dict], stream_callback=None):
        self.calls += 1
        text = f"{self.name} output {self.calls}"
        if stream_callback is not None:
            stream_callback("reasoning", f"{self.name} reasoning")
            stream_callback("content", text)
        response = SimpleNamespace(
            content=text,
            model=self.model,
            request_messages=messages,
            requested_reasoning_effort=self.reasoning_effort,
            provider_reasoning=f"{self.name} reasoning",
            reasoning_details=None,
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=3,
            cached_tokens=0,
            cost_usd=0.001,
            latency_s=0.01,
        )
        return text, response


def fake_agent_factory(role: str, raw: dict):
    return FakeAgent(role, raw)


def _project(tmp_path: Path, monkeypatch, *, experiment: str = "Araştırma Döngüsü"):
    monkeypatch.chdir(tmp_path)
    pm = ProjectManager()
    info = pm.create_project(
        title="Worker dispatch",
        project_id="worker-dispatch",
        problem="Frozen theorem problem",
        experiment=experiment,
    )
    return pm, info, pm.project_root(info.project_id)


def _agent(role: str, display_role: str | None = None) -> dict:
    return {
        "role": role,
        "display_role": display_role or role,
        "system_prompt": f"You are {role}",
        "model": "fake/model",
        "temperature": 0.2,
        "reasoning_effort": "medium",
    }


@pytest.mark.parametrize(
    ("method", "name", "agents", "optional", "param"),
    [
        (
            "research_loop",
            "Araştırma Döngüsü",
            [_agent("Teorisyen"), _agent("Sceptik")],
            {"Raporcu": _agent("Raporcu")},
            1,
        ),
        (
            "debate",
            "Tartışma",
            [_agent("Taraftar A"), _agent("Taraftar B")],
            {"Hakem": _agent("Hakem")},
            1,
        ),
        (
            "pipeline",
            "Zincir",
            [_agent("Araştırmacı"), _agent("Analist"), _agent("Eleştirmen")],
            {},
            0,
        ),
        (
            "panel",
            "Panel",
            [
                _agent("Panelist", "Panelist"),
                _agent("Panelist", "Panelist 2"),
                _agent("Panelist", "Panelist 3"),
            ],
            {"Sentezleyici": _agent("Sentezleyici")},
            0,
        ),
    ],
)
def test_non_theorem_worker_dispatch_completes_without_touching_research_ledger(
    tmp_path,
    monkeypatch,
    method,
    name,
    agents,
    optional,
    param,
):
    pm, info, root = _project(tmp_path, monkeypatch, experiment=name)
    state_path = root / "state.json"
    before = state_path.read_bytes()
    request = {
        "request_version": 2,
        "project_id": info.project_id,
        "project_uuid": info.project_uuid,
        "experiment_method": method,
        "experiment_name": name,
        "agents": agents,
        "optional_agents": optional,
        "param": param,
        "prompt": "Test prompt",
    }
    (root / "worker_request.json").write_text(json.dumps(request), encoding="utf-8")

    assert worker.run_project(info.project_id, agent_factory=fake_agent_factory) == 0
    assert before == state_path.read_bytes()
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "COMPLETED"
    assert (root / "worker_result.md").read_text(encoding="utf-8").strip()
    assert not (root / "run.lock").exists()
    run_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "summary.json").is_file()
    assert pm.get(info.project_id).status == "COMPLETED"


def test_missing_experiment_method_defaults_to_theorem_lab(tmp_path, monkeypatch):
    _pm, info, root = _project(tmp_path, monkeypatch, experiment="Teorem Araştırması")
    raw_agents = {
        role: _agent(role)
        for role in (
            "ResearchManager",
            "Theorist",
            "AdversarialCritic",
            "VerificationEngineer",
            "IndependentAuditor",
        )
    }
    request = {
        "request_version": 1,
        "project_id": info.project_id,
        "project_uuid": info.project_uuid,
        "problem": "legacy theorem request",
        "iterations": 1,
        "checkpoint_every": 1,
        "agents": raw_agents,
    }
    (root / "worker_request.json").write_text(json.dumps(request), encoding="utf-8")
    seen: dict[str, object] = {}

    class FakeTheoremLab:
        def __init__(self, trace, state, **_kwargs):
            self.controller = SimpleNamespace(lock=None)
            self.state = state

        def run(self, problem, **kwargs):
            seen["problem"] = problem
            seen["roles"] = set(kwargs)
            (self.state.root / "runtime.json").write_text(
                json.dumps({"status": "COMPLETED"}), encoding="utf-8"
            )
            return "legacy theorem result"

    monkeypatch.setattr(worker, "TheoremResearchLab", FakeTheoremLab)
    assert worker.run_project(info.project_id, agent_factory=fake_agent_factory) == 0
    assert seen["problem"] == "legacy theorem request"
    assert "manager" in seen["roles"]
    assert "legacy theorem result" in (root / "worker_result.md").read_text(encoding="utf-8")


def test_second_launch_is_rejected_while_project_lock_is_live(tmp_path, monkeypatch):
    _pm, info, root = _project(tmp_path, monkeypatch)
    request = {
        "request_version": 2,
        "project_id": info.project_id,
        "project_uuid": info.project_uuid,
        "experiment_method": "pipeline",
        "experiment_name": "Zincir",
        "agents": [_agent("Araştırmacı")],
        "optional_agents": {},
        "param": 0,
        "prompt": "Test",
    }
    write_worker_request(root, request)
    lock = ProjectRunLock(root)
    lock.acquire()
    try:
        with pytest.raises(ProjectBusyError):
            launch_worker(info.project_id)
    finally:
        lock.release()
        (root / "launch.guard").unlink(missing_ok=True)
