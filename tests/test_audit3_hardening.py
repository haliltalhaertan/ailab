from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess

import pytest

import lab.integrity as integrity
from lab.integrity import ProjectBusyError, ProjectRunLock, atomic_write_text, sha256_file
from lab.project_manager import ProjectManager
from lab.research_state import ResearchState
from lab.runtime_health import cleanup_stale_run
from lab.step_store import StepStore
from lab.tools import LeanTool, TropicalGridTool, Z3Tool
from lab.worker_launcher import write_worker_request


def test_atomic_write_retries_windows_style_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "runtime.json"
    real_replace = integrity.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(integrity.os, "replace", flaky_replace)
    atomic_write_text(target, "ok", attempts=5, initial_backoff_s=0)
    assert target.read_text(encoding="utf-8") == "ok"
    assert calls["n"] == 3


def test_dead_worker_running_state_is_stale_until_explicit_cleanup(tmp_path):
    pm = ProjectManager(tmp_path / "research_state", tmp_path / "runs")
    project = pm.create_project(title="Dead worker", project_id="dead-worker", problem="P", activate=False)
    root = pm.project_root(project.project_id)
    (root / "runtime.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "completed_iterations": 1,
                "heartbeat_at": "2000-01-01T00:00:00+00:00",
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (root / "run.lock").write_text(
        json.dumps({"pid": 99999999, "host": socket.gethostname(), "token": "dead"}),
        encoding="utf-8",
    )
    (root / "worker.json").write_text(
        json.dumps({"pid": 99999998, "run_id": "dead-run", "launched_at": "2000-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    info = pm.get(project.project_id)
    assert info.status == "STALE_RUNNING"
    persisted = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "RUNNING"

    cleaned = cleanup_stale_run(root)
    assert cleaned["status"] == "INTERRUPTED"
    assert not (root / "run.lock").exists()
    assert pm.get(project.project_id).status == "INTERRUPTED"


def test_live_lock_and_recent_heartbeat_keep_running_state(tmp_path):
    pm = ProjectManager(tmp_path / "research_state", tmp_path / "runs")
    project = pm.create_project(title="Live worker", project_id="live-worker", problem="P", activate=False)
    root = pm.project_root(project.project_id)
    (root / "runtime.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "heartbeat_at": "2999-01-01T00:00:00+00:00",
                "updated_at": "2999-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (root / "run.lock").write_text(
        json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": "live"}),
        encoding="utf-8",
    )
    assert pm.get(project.project_id).status == "RUNNING"


def test_project_lock_is_reentrant_for_same_instance(tmp_path):
    lock = ProjectRunLock(tmp_path / "project")
    with lock:
        assert lock.acquired
        with lock:
            assert lock.acquired
        assert lock.acquired
    assert not lock.acquired


def test_worker_request_cannot_overwrite_active_run(tmp_path):
    root = tmp_path / "project"
    with ProjectRunLock(root):
        with pytest.raises(ProjectBusyError):
            write_worker_request(root, {"project_id": "p"})
        assert not (root / "worker_request.json").exists()
        assert not (root / "launch.guard").exists()


def _binding_kwargs(item_id: str = "C-test", iteration: int = 1, claim: str = "claim") -> dict:
    return {
        "theorem_name": "bound",
        "theorem_type": "1 = 1",
        "item_id": item_id,
        "iteration": iteration,
        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
    }


@pytest.mark.parametrize(
    "source",
    [
        "theorem bound : 1 = 1 := by sorry",
        "axiom hidden : 1 = 1\ntheorem bound : 1 = 1 := hidden",
        "theorem bound : 1 = 1 := by native_decide",
        "set_option autoImplicit true\ntheorem bound : 1 = 1 := by rfl",
    ],
)
def test_lean_draft_rejects_proof_escape_hatches(tmp_path, source):
    tool = LeanTool(tmp_path / "formal")
    result = tool.draft_source("candidate.lean", source, **_binding_kwargs())
    assert not result.ok
    assert result.metadata.get("formal_verified") is False


def test_lean_draft_requires_exact_statement_binding(tmp_path):
    tool = LeanTool(tmp_path / "formal")
    result = tool.draft_source(
        "candidate.lean",
        "theorem unrelated : 1 = 1 := by rfl",
        **_binding_kwargs(),
    )
    assert not result.ok
    assert "theorem_name" in result.error


def test_lean_compiler_sorry_warning_is_not_formal_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    tool = LeanTool(tmp_path / "formal")
    binding = _binding_kwargs()
    draft = tool.draft_source(
        "candidate.lean",
        "theorem bound : 1 = 1 := by rfl",
        **binding,
    )
    assert draft.ok

    def fake_run(_candidate):
        return (
            subprocess.CompletedProcess(["lean"], 0, stdout="", stderr="warning: declaration uses 'sorry'"),
            "lean",
        )

    monkeypatch.setattr(tool, "_run_lean", fake_run)
    result = tool.check_file(
        "candidate.lean",
        expected_sha256=draft.metadata["lean_sha256"],
        expected_item_id=binding["item_id"],
        expected_iteration=binding["iteration"],
        expected_claim_sha256=binding["claim_sha256"],
        expected_theorem_name=binding["theorem_name"],
        expected_theorem_type=binding["theorem_type"],
    )
    assert not result.ok
    assert result.metadata["formal_verified"] is False


def test_lean_unexpected_axiom_is_not_formal_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    tool = LeanTool(tmp_path / "formal")
    binding = _binding_kwargs()
    draft = tool.draft_source(
        "candidate.lean",
        "theorem bound : 1 = 1 := by rfl",
        **binding,
    )
    assert draft.ok
    calls = {"n": 0}

    def fake_run(_candidate):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(["lean"], 0, stdout="", stderr=""), "lean"
        return (
            subprocess.CompletedProcess(
                ["lean"],
                0,
                stdout="'bound' depends on axioms: [Bad.magic]",
                stderr="",
            ),
            "lean",
        )

    monkeypatch.setattr(tool, "_run_lean", fake_run)
    result = tool.check_file(
        "candidate.lean",
        expected_sha256=draft.metadata["lean_sha256"],
        expected_item_id=binding["item_id"],
        expected_iteration=binding["iteration"],
        expected_claim_sha256=binding["claim_sha256"],
        expected_theorem_name=binding["theorem_name"],
        expected_theorem_type=binding["theorem_type"],
    )
    assert not result.ok
    assert result.metadata["axioms_verified"] is False


def test_proven_ledger_record_is_sealed_and_live_sha_rechecked(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_EVIDENCE_HMAC_KEY", "unit-test-external-key")
    state = ResearchState(tmp_path / "project")
    item = state.add_item("conjecture", "Bound", "claim")
    candidate_dir = state.root / "formal" / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_dir / f"iter-1-{item.id}.lean"
    candidate.write_text("theorem bound : 1 = 1 := by rfl\n", encoding="utf-8")
    metadata = {
        "formal_verified": True,
        "formal_binding_verified": True,
        "axioms_verified": True,
        "source_clean": True,
        "item_id": item.id,
        "iteration": 1,
        "claim_sha256": hashlib.sha256(item.claim.encode("utf-8")).hexdigest(),
        "lean_file": candidate.name,
        "lean_sha256": sha256_file(candidate),
        "theorem_name": "bound",
        "theorem_type": "1 = 1",
        "axioms": [],
    }
    proven = state.update_item(item.id, status="PROVEN", metadata=metadata)
    assert proven.status == "PROVEN"
    assert proven.metadata.get("proof_seal")
    assert proven.metadata.get("evidence_key_mode") == "EXTERNAL_ENV"
    assert state.get(item.id).status == "PROVEN"

    candidate.write_text("theorem bound : 2 = 2 := by rfl\n", encoding="utf-8")
    reread = state.get(item.id)
    assert reread.status == "PROOF_CANDIDATE"
    assert "SHA-256 changed" in reread.metadata.get("integrity_warning", "")


def test_step_store_rejects_direct_sqlite_payload_tamper(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_EVIDENCE_HMAC_KEY", "unit-test-external-key")
    store = StepStore(tmp_path / "project")
    store.put_step("iter:1:tool", {"status": "COMPLETE", "fingerprint": "abc", "result": {"ok": False}})
    assert store.get_step("iter:1:tool") is not None

    with sqlite3.connect(store.path) as con:
        row = con.execute("SELECT payload_json FROM steps WHERE step_key=?", ("iter:1:tool",)).fetchone()
        payload = json.loads(row[0])
        payload["result"] = {"ok": True, "tool": "lean", "metadata": {"formal_verified": True}}
        con.execute(
            "UPDATE steps SET payload_json=? WHERE step_key=?",
            (json.dumps(payload), "iter:1:tool"),
        )
    assert store.get_step("iter:1:tool") is None


def test_empty_z3_query_is_not_computation_evidence():
    result = Z3Tool().check("")
    assert not result.ok
    assert result.metadata["assertion_count"] == 0


def test_tropical_checker_reports_size_and_rejects_wrong_provenance_structure():
    tool = TropicalGridTool()
    good = {
        "n": 2,
        "gates": [{"id": "e", "op": "edge", "u": 1, "v": 2}],
        "output": "e",
    }
    passed = tool.check(good, [0, 1])
    assert passed.ok
    assert passed.metadata["provenance_structure_ok"] is True
    assert passed.metadata["gate_count"] == 1

    bad = {
        "n": 2,
        "gates": [
            {"id": "e", "op": "edge", "u": 1, "v": 2},
            {"id": "twice", "op": "add", "args": ["e", "e"]},
        ],
        "output": "twice",
    }
    failed = tool.check(bad, [0, 1])
    assert not failed.ok
    assert failed.metadata["status"] == "STRUCTURE_MISMATCH"
    assert failed.metadata["gate_count"] == 2