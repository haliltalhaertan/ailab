"""Recovery must preserve evidence and reject altered continuation context."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from lab.integrity import ProjectBusyError, ProjectRunLock
from lab.run_controller import ResearchPaused, RunController, set_research_phase
from lab.runtime_health import cleanup_stale_run
from lab.runtime_health import normalize_runtime
from lab.step_store import StepStore
from lab.worker import _mark_runtime_error


@pytest.mark.parametrize("raw", [b'{broken', b'\xff', b'[]', b'null', b'{}'])
@pytest.mark.parametrize("operation", ["update", "heartbeat", "phase", "error"])
def test_corrupt_runtime_is_preserved(tmp_path, raw, operation):
    controller = RunController(tmp_path, Mock())
    controller.set_runtime(status="RUNNING", completed_iterations=7, next_task="keep this")
    controller.runtime_path.write_bytes(raw)
    if operation == "error":
        _mark_runtime_error(tmp_path, RuntimeError("test"))
        assert (tmp_path / "runtime_error.json").exists()
    else:
        with pytest.raises(ResearchPaused):
            if operation == "update":
                controller.set_runtime(status="PAUSED_ERROR")
            elif operation == "heartbeat":
                controller.heartbeat(min_interval_s=0)
            else:
                set_research_phase(tmp_path, "PROOF")
    assert controller.runtime_path.read_bytes() == raw
    assert normalize_runtime(tmp_path)["status"] == "PAUSED_ERROR"


def test_valid_runtime_progress_survives(tmp_path):
    controller = RunController(tmp_path, Mock())
    controller.set_runtime(status="RUNNING", completed_iterations=7, next_task="next")
    controller.heartbeat(min_interval_s=0)
    set_research_phase(tmp_path, "PROOF")
    assert controller.runtime()["completed_iterations"] == 7
    assert controller.runtime()["next_task"] == "next"


@pytest.mark.parametrize("column,value", [
    ("ledger_context", "altered"), ("ledger_revision", "altered"),
    ("payload_json", '{"next_task":"altered"}'), ("iteration", 2),
])
def test_snapshot_tamper_stops_resume(tmp_path, column, value):
    store = StepStore(tmp_path)
    store.put_iteration_snapshot(1, ledger_revision="r", ledger_context="ctx", payload={"next_task": "next"})
    with closing(sqlite3.connect(store.path)) as con, con:
        con.execute(f"UPDATE iteration_snapshots SET {column}=?", (value,))
    with pytest.raises(ResearchPaused):
        store.get_iteration_snapshot(2 if column == "iteration" else 1)


def test_partial_tamper_cannot_enter_prompt(tmp_path):
    store = StepStore(tmp_path)
    store.put_partial("a", {"content": "original", "fingerprint": "fp"})
    with closing(sqlite3.connect(store.path)) as con, con:
        con.execute("UPDATE partials SET payload_json=?", ('{"content":"altered"}',))
    with pytest.raises(ResearchPaused):
        store.get_partial("a")
    assert store.counts()["partials"] == 0
    assert store.list_partials()[0].get("content") is None


def test_malformed_signature_is_rejected_without_breaking_inspection(tmp_path):
    store = StepStore(tmp_path)
    store.put_partial("a", {"content": "original"})
    with closing(sqlite3.connect(store.path)) as con, con:
        payload = json.loads(con.execute("SELECT payload_json FROM partials").fetchone()[0])
        payload["_record_signature"] = "é" * 64
        con.execute("UPDATE partials SET payload_json=?", (json.dumps(payload),))
    assert store.list_partials()[0]["sealed"] is False
    with pytest.raises(ResearchPaused):
        store.get_partial("a")


def test_signed_records_round_trip_and_update(tmp_path):
    store = StepStore(tmp_path)
    store.put_partial("a", {"content": "original", "fingerprint": "fp"})
    store.put_iteration_snapshot(1, ledger_revision="r", ledger_context="ctx", payload={"next_task": "next"})
    assert store.get_partial("a")["content"] == "original"
    store.update_iteration_payload(1, next_task="updated")
    assert StepStore(tmp_path).get_iteration_snapshot(1)["next_task"] == "updated"


@pytest.mark.parametrize("column,value", [("status", "COMPLETE"), ("fingerprint", "forged")])
def test_step_columns_must_match_signed_payload(tmp_path, column, value):
    store = StepStore(tmp_path)
    store.put_step("a", {"status": "PARTIAL", "fingerprint": "fp"})
    with closing(sqlite3.connect(store.path)) as con, con:
        con.execute(f"UPDATE steps SET {column}=?", (value,))
    assert store.get_step("a") is None
    assert store.counts()["complete_steps"] == 0
    assert store.list_steps()[0]["sealed"] is False
    assert store.list_steps()[0]["status"] == "INVALID"


def test_unsigned_legacy_cache_is_preserved_but_not_trusted(tmp_path):
    legacy = {"a": {"status": "COMPLETE", "content": "unverified"}}
    (tmp_path / "step_cache.json").write_text(json.dumps(legacy))
    store = StepStore(tmp_path)
    assert store.get_step("a") is None
    assert StepStore(tmp_path).get_step("a") is None
    with closing(sqlite3.connect(store.path)) as con:
        assert con.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1


def test_connection_is_closed_after_context(tmp_path):
    store = StepStore(tmp_path)
    with store._connect() as con:
        con.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")


def test_stale_heartbeat_does_not_allow_live_owner_cleanup(tmp_path):
    controller = RunController(tmp_path, Mock())
    controller.set_runtime(status="RUNNING", completed_iterations=7)
    raw = controller.runtime()
    raw["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
    controller.runtime_path.write_text(json.dumps(raw))
    before = controller.runtime_path.read_bytes()
    with ProjectRunLock(tmp_path):
        owner = (tmp_path / "run.lock").read_bytes()
        with pytest.raises((ProjectBusyError, RuntimeError)):
            cleanup_stale_run(tmp_path)
        assert (tmp_path / "run.lock").read_bytes() == owner
        assert controller.runtime_path.read_bytes() == before


def test_force_stop_does_not_overwrite_replacement_worker(tmp_path, monkeypatch):
    import lab.ui_project_settings as ui
    controller = RunController(tmp_path, Mock())
    controller.set_runtime(status="RUNNING", completed_iterations=8)
    before = controller.runtime_path.read_bytes()
    monkeypatch.setattr(ui, "_worker_pid", lambda _: 12345)
    monkeypatch.setattr(ui, "process_alive", lambda _: False)
    monkeypatch.setattr(ui.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "_kill_process_group", lambda *a: None)
    with ProjectRunLock(tmp_path):
        lock = (tmp_path / "run.lock").read_bytes()
        assert ui.force_stop_worker(tmp_path, wait_s=0) is False
        assert (tmp_path / "run.lock").read_bytes() == lock
        assert controller.runtime_path.read_bytes() == before


@pytest.mark.parametrize("corrupt", ["runtime", "snapshot"])
def test_corruption_pauses_engine_before_any_new_model_call(tmp_path, corrupt):
    from lab.research_state import ResearchState
    from test_resumable_research import make_agents, run_once

    state = ResearchState(tmp_path / "state")
    run_once(tmp_path, state, make_agents(critic_fail=True), "first")
    if corrupt == "runtime":
        runtime = state.root / "runtime.json"
        runtime.write_bytes(b'{broken')
    else:
        with closing(sqlite3.connect(state.root / "research_steps.sqlite3")) as con, con:
            con.execute("UPDATE iteration_snapshots SET ledger_context='altered'")
    agents = make_agents()
    result = run_once(tmp_path, state, agents, "resume")
    assert "beklemeye" in result
    assert all(agent.calls == 0 for agent in agents.values())
    if corrupt == "runtime":
        assert runtime.read_bytes() == b'{broken'
        assert (state.root / "runtime_error.json").exists()
