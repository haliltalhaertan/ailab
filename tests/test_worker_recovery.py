from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import lab.integrity as integrity
import lab.worker as worker
from lab import TheoremResearchLab
from lab.integrity import ProjectBusyError, ProjectRunLock, atomic_write_json
from lab.project_manager import ProjectManager
from lab.research_state import ResearchState


class _NoopTrace:
    closed = False

    def log(self, *args, **kwargs):
        del args, kwargs


def test_atomic_json_retries_on_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "runtime.json"
    real_replace = integrity.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(integrity.os, "replace", flaky_replace)
    monkeypatch.setattr(integrity.time, "sleep", lambda _seconds: None)
    atomic_write_json(target, {"status": "RUNNING"})

    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "RUNNING"


def test_lock_is_held_before_any_project_write(tmp_path):
    state = ResearchState(tmp_path / "project")
    trace = _NoopTrace()
    lab = TheoremResearchLab(trace, state)
    lab.controller.config_path.write_text("config-sentinel", encoding="utf-8")
    lab.controller.stop_path.write_text("stop-sentinel", encoding="utf-8")

    with ProjectRunLock(state.root):
        with pytest.raises(ProjectBusyError):
            lab.run(
                "P",
                manager=None,
                proposer=None,
                critic=None,
                verifier=None,
                auditor=None,
            )

    assert lab.controller.config_path.read_text(encoding="utf-8") == "config-sentinel"
    assert lab.controller.stop_path.read_text(encoding="utf-8") == "stop-sentinel"


def _worker_request(project_uuid: str) -> dict:
    roles = {
        "ResearchManager",
        "Theorist",
        "AdversarialCritic",
        "VerificationEngineer",
        "IndependentAuditor",
    }
    return {
        "request_version": 1,
        "project_uuid": project_uuid,
        "problem": "P",
        "iterations": 1,
        "checkpoint_every": 1,
        "agents": {
            role: {
                "name": role,
                "system_prompt": "test",
                "model": "test/model",
                "temperature": 0.0,
            }
            for role in roles
        },
    }


def test_worker_json_contains_actual_in_process_pid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pm = ProjectManager()
    project = pm.create_project(
        title="Worker PID",
        project_id="worker-pid",
        problem="P",
        activate=False,
    )
    root = pm.project_root(project.project_id)
    atomic_write_json(root / "worker_request.json", _worker_request(project.project_uuid))

    class FakeLab:
        def __init__(self, trace, state, **kwargs):
            del trace, kwargs
            self.state = state
            self.controller = SimpleNamespace(lock=None)

        def run(self, *args, **kwargs):
            del args, kwargs
            atomic_write_json(
                self.state.root / "runtime.json",
                {"status": "COMPLETED", "completed_iterations": 1},
            )
            return "done"

    monkeypatch.setattr(worker, "TheoremResearchLab", FakeLab)
    assert worker.run_project(project.project_id) == 0

    identity = json.loads((root / "worker.json").read_text(encoding="utf-8"))
    assert identity["pid"] == os.getpid()
    assert set(identity) == {"pid", "run_id", "launched_at"}
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["status"] == "READY"
    assert json.loads((root / "runtime.json").read_text(encoding="utf-8"))["status"] == "COMPLETED"


def test_busy_worker_does_not_overwrite_worker_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pm = ProjectManager()
    project = pm.create_project(
        title="Busy worker",
        project_id="busy-worker",
        problem="P",
        activate=False,
    )
    root = pm.project_root(project.project_id)
    atomic_write_json(root / "worker_request.json", _worker_request(project.project_uuid))
    sentinel = {"pid": 123, "run_id": "active", "launched_at": "sentinel"}
    atomic_write_json(root / "worker.json", sentinel)

    with ProjectRunLock(root):
        assert worker.run_project(project.project_id) == 3

    assert json.loads((root / "worker.json").read_text(encoding="utf-8")) == sentinel
    assert (root / "worker_busy.json").exists()
