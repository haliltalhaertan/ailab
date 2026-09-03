from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import lab.worker as worker
from lab.integrity import ProjectBusyError, ProjectRunLock
from lab.project_manager import ProjectManager
from lab.run_controller import RunController
from lab.worker_launcher import launch_worker, write_worker_request


class FakeAgent:
    def __init__(self, name: str, raw: dict):
        self.name = str(raw.get("display_role") or raw.get("name") or name)
        self.model = str(raw.get("model") or "fake/model")
        self.system_prompt = str(raw.get("system_prompt") or raw.get("prompt") or "")
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


def _theorem_request(info) -> dict:
    return {
        "request_version": 2,
        "project_id": info.project_id,
        "project_uuid": info.project_uuid,
        "experiment_method": "theorem_lab",
        "experiment_name": "Teorem Araştırması",
        "problem": "theorem problem",
        "iterations": 1,
        "checkpoint_every": 1,
        "agents": {
            role: _agent(role)
            for role in (
                "ResearchManager",
                "Theorist",
                "AdversarialCritic",
                "VerificationEngineer",
                "IndependentAuditor",
            )
        },
    }


@pytest.mark.parametrize(
    ("method", "name", "agents", "optional", "param"),
    [
        ("research_loop", "Araştırma Döngüsü", [_agent("Teorisyen"), _agent("Sceptik")], {"Raporcu": _agent("Raporcu")}, 1),
        ("debate", "Tartışma", [_agent("Taraftar A"), _agent("Taraftar B")], {"Hakem": _agent("Hakem")}, 1),
        ("pipeline", "Zincir", [_agent("Araştırmacı"), _agent("Analist"), _agent("Eleştirmen")], {}, 0),
        ("panel", "Panel", [_agent("Panelist", "Panelist"), _agent("Panelist", "Panelist 2"), _agent("Panelist", "Panelist 3")], {"Sentezleyici": _agent("Sentezleyici")}, 0),
    ],
)
def test_non_theorem_worker_dispatch_completes_without_touching_research_ledger(
    tmp_path, monkeypatch, method, name, agents, optional, param
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

    exit_code = worker.run_project(info.project_id, agent_factory=fake_agent_factory)
    worker_result = (root / "worker_result.md").read_text(encoding="utf-8")
    assert exit_code == 0, worker_result
    assert before == state_path.read_bytes()
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "COMPLETED"
    assert worker_result.strip()
    assert not (root / "run.lock").exists()
    run_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "summary.json").is_file()
    assert (run_dirs[0] / "stream.jsonl.gz").is_file()
    assert pm.get(info.project_id).status == "COMPLETED"


def test_worker_heartbeat_thread_stops_before_final_completed_status(tmp_path, monkeypatch):
    _pm, info, root = _project(tmp_path, monkeypatch, experiment="Zincir")
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
    (root / "worker_request.json").write_text(json.dumps(request), encoding="utf-8")

    ordering: list[str] = []
    bridges = []
    original_heartbeat = RunController.heartbeat
    original_set_runtime = RunController.set_runtime
    real_bridge = worker.WorkerRuntimeBridge

    def recording_heartbeat(self, *args, **kwargs):
        ordering.append("heartbeat")
        return original_heartbeat(self, *args, **kwargs)

    def recording_set_runtime(self, **updates):
        result = original_set_runtime(self, **updates)
        if updates.get("status") == "COMPLETED":
            ordering.append("final_completed")
        return result

    class CapturingBridge(real_bridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            bridges.append(self)

    monkeypatch.setattr(RunController, "heartbeat", recording_heartbeat)
    monkeypatch.setattr(RunController, "set_runtime", recording_set_runtime)
    monkeypatch.setattr(worker, "WorkerRuntimeBridge", CapturingBridge)

    assert worker.run_project(info.project_id, agent_factory=fake_agent_factory) == 0
    final_index = ordering.index("final_completed")
    assert "heartbeat" not in ordering[final_index + 1 :]
    assert bridges
    assert bridges[-1]._thread is not None
    assert bridges[-1]._thread.is_alive() is False


def test_theorem_worker_background_heartbeat_survives_blocking_step_and_stops_at_final(tmp_path, monkeypatch):
    _pm, info, root = _project(tmp_path, monkeypatch, experiment="Teorem Araştırması")
    (root / "worker_request.json").write_text(json.dumps(_theorem_request(info)), encoding="utf-8")
    seen = {}

    class SlowTheoremLab:
        def __init__(self, trace, state, **_kwargs):
            self.controller = RunController(state.root, trace)

        def run(self, _problem, **_kwargs):
            first = self.controller.set_runtime(status="RUNNING")["heartbeat_at"]
            time.sleep(0.35)
            middle = self.controller.runtime()["heartbeat_at"]
            seen["first"] = first
            seen["middle"] = middle
            self.controller.set_runtime(status="COMPLETED")
            return "slow theorem result"

    monkeypatch.setattr(worker, "TheoremResearchLab", SlowTheoremLab)
    monkeypatch.setattr(worker.WorkerRuntimeBridge, "HEARTBEAT_POLL_S", 0.03)
    monkeypatch.setattr(worker.WorkerRuntimeBridge, "HEARTBEAT_MIN_INTERVAL_S", 0.08)

    assert worker.run_project(info.project_id, agent_factory=fake_agent_factory) == 0
    assert seen["middle"] != seen["first"]
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "COMPLETED"
    final_heartbeat = runtime["heartbeat_at"]
    time.sleep(0.12)
    after = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert after["heartbeat_at"] == final_heartbeat


def test_missing_experiment_method_defaults_to_theorem_lab(tmp_path, monkeypatch):
    _pm, info, root = _project(tmp_path, monkeypatch, experiment="Teorem Araştırması")
    request = _theorem_request(info)
    request.pop("experiment_method")
    request["request_version"] = 1
    request["problem"] = "legacy theorem request"
    (root / "worker_request.json").write_text(json.dumps(request), encoding="utf-8")
    seen: dict[str, object] = {}

    class FakeTheoremLab:
        def __init__(self, trace, state, **_kwargs):
            self.controller = RunController(state.root, trace)

        def run(self, problem, **kwargs):
            seen["problem"] = problem
            seen["roles"] = set(kwargs)
            self.controller.set_runtime(status="COMPLETED")
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
