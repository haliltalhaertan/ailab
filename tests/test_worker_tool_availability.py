import json
from types import SimpleNamespace

from lab.tool_registry import EFFECTIVE_AVAILABILITY_ENV, ToolRegistry
from lab.worker import (
    _persist_tool_availability_in_run_config,
    _tool_availability_for_run,
    _trace_tool_availability,
)
from lab.trace import Trace


class FakeRegistry:
    def __init__(self, measured):
        self.measured = measured
        self.bound = None

    def availability(self):
        return {name: dict(raw) for name, raw in self.measured.items()}

    def set_effective_availability(self, value):
        self.bound = value


class FakeWorkspace:
    def __init__(self, available: bool):
        self.execution_available = available
        self.container_engine = "docker" if available else ""


def _lab(measured, *, container=False):
    return SimpleNamespace(
        registry=FakeRegistry(measured),
        code_workspace=FakeWorkspace(container),
    )


def _row(open_: bool, reason: str):
    return {"available": open_, "reason": reason}


def test_new_run_declares_current_capabilities_and_persists_sidecar(tmp_path):
    lab = _lab(
        {
            "lean_draft": _row(False, "Lean off"),
            "z3": _row(True, "Z3 on"),
            "script": _row(True, "scripts on"),
        }
    )

    snapshot = _tool_availability_for_run(tmp_path, lab, {"status": "COMPLETED"})

    assert snapshot["resumed_snapshot"] is False
    assert snapshot["declared_tool_availability"]["lean_draft"]["available"] is False
    assert snapshot["effective_tool_availability"]["z3"]["available"] is True
    assert snapshot["effective_tool_availability"]["code_experiment"]["available"] is False
    assert lab.registry.bound == snapshot["effective_tool_availability"]
    saved = json.loads((tmp_path / "tool_availability.json").read_text(encoding="utf-8"))
    assert saved["declared_tool_availability"] == snapshot["declared_tool_availability"]


def test_resume_does_not_widen_when_a_new_tool_appears(tmp_path):
    first = _lab({"lean_draft": _row(False, "Lean off"), "z3": _row(True, "Z3 on")})
    _tool_availability_for_run(tmp_path, first, {"status": "NEW"})

    resumed = _lab({"lean_draft": _row(True, "Lean installed"), "z3": _row(True, "Z3 on")})
    snapshot = _tool_availability_for_run(tmp_path, resumed, {"status": "PAUSED_ERROR"})

    assert snapshot["resumed_snapshot"] is True
    assert snapshot["runtime_tool_availability"]["lean_draft"]["available"] is True
    assert snapshot["declared_tool_availability"]["lean_draft"]["available"] is False
    assert snapshot["effective_tool_availability"]["lean_draft"]["available"] is False
    assert "run başında kapalı" in snapshot["effective_tool_availability"]["lean_draft"]["reason"]


def test_resume_narrows_when_a_declared_tool_disappears(tmp_path):
    first = _lab({"lean_draft": _row(True, "Lean on"), "z3": _row(True, "Z3 on")})
    _tool_availability_for_run(tmp_path, first, {"status": "NEW"})

    resumed = _lab({"lean_draft": _row(False, "Lean missing"), "z3": _row(True, "Z3 on")})
    snapshot = _tool_availability_for_run(tmp_path, resumed, {"status": "INTERRUPTED"})

    assert snapshot["declared_tool_availability"]["lean_draft"]["available"] is True
    assert snapshot["effective_tool_availability"]["lean_draft"]["available"] is False
    assert "runtime daralması" in snapshot["effective_tool_availability"]["lean_draft"]["reason"]


def test_completed_run_starts_a_fresh_capability_snapshot(tmp_path):
    first = _lab({"lean_draft": _row(False, "Lean off")})
    _tool_availability_for_run(tmp_path, first, {"status": "NEW"})

    second = _lab({"lean_draft": _row(True, "Lean installed")})
    snapshot = _tool_availability_for_run(tmp_path, second, {"status": "COMPLETED"})

    assert snapshot["resumed_snapshot"] is False
    assert snapshot["declared_tool_availability"]["lean_draft"]["available"] is True
    assert snapshot["effective_tool_availability"]["lean_draft"]["available"] is True


def test_availability_is_copied_into_run_config_without_touching_contract_hash(tmp_path):
    config = {
        "config_version": 3,
        "contract_hash": "frozen-contract-hash",
        "problem": "P",
    }
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    snapshot = {
        "declared_tool_availability": {"lean_draft": _row(False, "off")},
        "runtime_tool_availability": {"lean_draft": _row(False, "off")},
        "effective_tool_availability": {"lean_draft": _row(False, "off")},
    }

    _persist_tool_availability_in_run_config(tmp_path, snapshot)
    updated = json.loads((tmp_path / "run_config.json").read_text(encoding="utf-8"))

    assert updated["config_version"] == 4
    assert updated["contract_hash"] == "frozen-contract-hash"
    assert updated["effective_tool_availability"]["lean_draft"]["available"] is False


def test_trace_records_narrowing_and_refuses_silent_widening(tmp_path):
    trace = Trace("availability", out_dir=tmp_path / "runs")
    snapshot = {
        "declared_tool_availability": {
            "lean_draft": _row(True, "declared on"),
            "script": _row(False, "declared off"),
        },
        "runtime_tool_availability": {
            "lean_draft": _row(False, "missing"),
            "script": _row(True, "now installed"),
        },
        "effective_tool_availability": {
            "lean_draft": _row(False, "runtime daralması"),
            "script": _row(False, "run başında kapalı"),
        },
        "resumed_snapshot": True,
    }

    _trace_tool_availability(trace, snapshot)
    trace.close()
    text = trace.path.read_text(encoding="utf-8")

    assert '"type": "tool_availability"' in text
    assert '"type": "tool_availability_narrowed"' in text
    assert '"type": "tool_availability_not_widened"' in text


def test_registry_defaults_can_receive_worker_effective_universe(tmp_path, monkeypatch):
    snapshot = {
        "lean_draft": _row(False, "frozen off"),
        "z3": _row(False, "frozen off"),
        "script": _row(False, "frozen off"),
        "tropical_grid": _row(True, "built-in"),
    }
    monkeypatch.setenv(EFFECTIVE_AVAILABILITY_ENV, json.dumps(snapshot))
    monkeypatch.setattr("lab.tool_registry.shutil.which", lambda _name: "fake")
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")

    registry = ToolRegistry()

    assert registry.is_available("lean_draft") is False
    assert registry.is_available("tropical_grid") is True
