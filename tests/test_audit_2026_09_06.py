"""Regression tests for the 2026-09-06 technical audit findings A01-A08."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

import pytest

from lab.client import LLMResponse
from lab.integrity import ProjectBusyError, ProjectRunLock, content_fingerprint
from lab.research_state import LedgerReadError, ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.tools import LeanTool, ResearchToolbox, TropicalGridTool
from lab.trace import Trace
from lab.ui_model import consume_log_chunk


class EmptyLiterature:
    def search(self, query, limit=8):
        return []


class FakeToolbox:
    def execute(self, request):
        return None


class FakeAgent:
    def __init__(self, name, output, *, fail=False, details=None):
        self.name = name
        self.system_prompt = f"system {name}"
        self.model = f"fake/{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.output = output
        self.fail = fail
        self.details = details
        self.calls = 0

    def respond(self, messages, stream_callback=None):
        self.calls += 1
        if self.details is not None and stream_callback:
            stream_callback("reasoning_details", self.details)
        if self.fail:
            raise RuntimeError("404 model endpoint missing")
        if stream_callback:
            stream_callback("content", self.output[:5])
        response = LLMResponse(
            content=self.output,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_s=0.01,
            cost_usd=0.001,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )
        return self.output, response


def _agents(*, tool_request=None, critic_fail=False, auditor_fail=False, proposer=None):
    proposal = {
        "title": "T",
        "claim": "C",
        "strategy": "S",
        "evidence_needed": [],
        "tool_request": tool_request or {"tool": "none"},
    }
    return {
        "manager": FakeAgent("ResearchManager", '{"decision":"KEEP","status":"OPEN","reason":"ok","next_task":"next"}'),
        "proposer": proposer or FakeAgent("Theorist", json.dumps(proposal)),
        "critic": FakeAgent("AdversarialCritic", '{"verdict":"KEEP","reason":"no counterexample","counterexample":""}', fail=critic_fail),
        "verifier": FakeAgent("VerificationEngineer", '{"verdict":"PASS","reason":"ok","formal_proof_required":true,"counterexample":""}'),
        "auditor": FakeAgent("IndependentAuditor", "PASS", fail=auditor_fail),
    }


def _run(tmp_path, state, agents, name, *, toolbox=None, checkpoint_every=99):
    trace = Trace(name, out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox or FakeToolbox(), max_retries=1)
    result = lab.run(
        "frozen problem",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=checkpoint_every,
    )
    trace.close()
    return lab, trace, result


def _runtime(state):
    return json.loads((state.root / "runtime.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- A01


def test_a01_lean_binds_actual_theorem_type(tmp_path):
    tool = LeanTool(root=tmp_path / "formal")
    decoy = "def unrelated : Prop := 1 = 2\ntheorem bound : True := True.intro\n"
    binding = {"item_id": "C-1", "iteration": 1, "claim_hash": "a" * 64}

    rejected = tool.draft_source("decoy.lean", decoy, theorem_name="bound", theorem_type="1 = 2", **binding)
    assert rejected.ok is False
    assert "theorem_type" in rejected.error
    assert not (tmp_path / "formal" / "candidates" / "decoy.lean").exists()

    hypothesis = "theorem bound (h : 1 = 2) : True := trivial\n"
    rejected = tool.draft_source("hyp.lean", hypothesis, theorem_name="bound", theorem_type="1 = 2", **binding)
    assert rejected.ok is False

    accepted = tool.draft_source("ok.lean", decoy, theorem_name="bound", theorem_type="True", **binding)
    assert accepted.ok is True
    assert accepted.metadata["theorem_conclusion"] == "True"

    binders = "theorem foo (n : Nat) : n + 0 = n := by simp\n"
    assert tool.draft_source("b1.lean", binders, theorem_name="foo", theorem_type="n + 0 = n", **binding).ok is True
    assert tool.draft_source("b2.lean", binders, theorem_name="foo", theorem_type="∀ (n : Nat), n + 0 = n", **binding).ok is True
    assert tool.draft_source("b3.lean", binders, theorem_name="foo", theorem_type="Nat", **binding).ok is False


def test_a01_check_file_re_elaborates_statement_with_lean(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    tool = LeanTool(root=tmp_path / "formal")
    source = "theorem bound (n : Nat) : n + 0 = n := by simp\n"
    binding = {"item_id": "C-1", "iteration": 1, "claim_hash": "b" * 64}
    draft = tool.draft_source("bound.lean", source, theorem_name="bound", theorem_type="n + 0 = n", **binding)
    assert draft.ok is True

    compiled: list[str] = []

    def fake_run(_self, candidate):
        compiled.append(Path(candidate).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(["lean"], 0, stdout="'bound' does not depend on any axioms", stderr=""), "lean"

    monkeypatch.setattr(LeanTool, "_run_lean", fake_run)
    result = tool.check_file(
        "bound.lean",
        expected_sha256=draft.metadata["lean_sha256"],
        expected_item_id="C-1",
        expected_iteration=1,
        expected_claim_hash="b" * 64,
        expected_theorem_name="bound",
        expected_theorem_type="n + 0 = n",
    )
    assert result.ok is True
    assert result.metadata["theorem_statement_verified"] is True
    audit_source = compiled[-1]
    assert "example : (∀ (n : Nat), n + 0 = n) := @bound" in audit_source
    assert "#print axioms bound" in audit_source

    def failing_probe(_self, candidate):
        text = Path(candidate).read_text(encoding="utf-8")
        if "example : (" in text:
            return subprocess.CompletedProcess(["lean"], 1, stdout="", stderr="type mismatch"), "lean"
        return subprocess.CompletedProcess(["lean"], 0, stdout="", stderr=""), "lean"

    monkeypatch.setattr(LeanTool, "_run_lean", failing_probe)
    result = tool.check_file(
        "bound.lean",
        expected_sha256=draft.metadata["lean_sha256"],
        expected_item_id="C-1",
        expected_iteration=1,
        expected_claim_hash="b" * 64,
        expected_theorem_name="bound",
        expected_theorem_type="n + 0 = n",
    )
    assert result.ok is False
    assert result.metadata["formal_verified"] is False
    assert result.metadata["theorem_statement_verified"] is False


# ---------------------------------------------------------------- A02


def _dead_lock_payload():
    child = subprocess.Popen(["python", "-c", "pass"])
    child.wait()
    return {"token": "dead-owner", "pid": child.pid, "host": socket.gethostname(), "created_at_epoch": time.time()}


def test_a02_stale_lock_reclamation_preserves_new_owner(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    lock_path = root / "run.lock"
    lock_path.write_text(json.dumps(_dead_lock_payload()), encoding="utf-8")

    first = ProjectRunLock(root)
    second = ProjectRunLock(root)
    original_stale = ProjectRunLock._stale

    def racing_stale(self, raw):
        stale = original_stale(self, raw)
        # The second worker observes the same stale lock and wins the race
        # between the first worker's observation and its reclamation.
        if self is first and stale and not second.acquired:
            second.acquire()
        return stale

    monkeypatch.setattr(ProjectRunLock, "_stale", racing_stale)
    with pytest.raises(ProjectBusyError):
        first.acquire()

    assert first.acquired is False
    assert second.acquired is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == second.token
    assert [p.name for p in root.iterdir()] == ["run.lock"]
    second.release()
    assert not lock_path.exists()


def test_a02_plain_stale_lock_is_still_reclaimed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "run.lock").write_text(json.dumps(_dead_lock_payload()), encoding="utf-8")
    lock = ProjectRunLock(root)
    lock.acquire()
    try:
        assert json.loads((root / "run.lock").read_text(encoding="utf-8"))["token"] == lock.token
        assert [p.name for p in root.iterdir()] == ["run.lock"]
    finally:
        lock.release()


# ---------------------------------------------------------------- A03


def test_a03_corrupt_ledger_is_not_silently_replaced(tmp_path):
    state = ResearchState(tmp_path / "state")
    state.add_item("conjecture", "old", "old claim")
    good = state.state_path.read_text(encoding="utf-8")
    corrupt = good[: len(good) // 2]
    state.state_path.write_text(corrupt, encoding="utf-8")

    fresh = ResearchState(tmp_path / "state")
    with pytest.raises(LedgerReadError):
        fresh.add_item("conjecture", "new", "new claim")
    with pytest.raises(LedgerReadError):
        fresh.list_items()
    assert state.state_path.read_text(encoding="utf-8") == corrupt

    state.state_path.write_text(good, encoding="utf-8")
    assert [item.title for item in fresh.list_items()] == ["old"]


def test_a03_missing_ledger_still_starts_empty(tmp_path):
    state = ResearchState(tmp_path / "new-state")
    assert state.list_items() == []
    state.add_item("conjecture", "first", "claim")
    assert [item.title for item in state.list_items()] == ["first"]


# ---------------------------------------------------------------- A04


def test_a04_interrupted_checkpoint_is_resumed(tmp_path):
    state = ResearchState(tmp_path / "state")
    first = _agents(auditor_fail=True)
    lab1, _trace, result1 = _run(tmp_path, state, first, "first", checkpoint_every=1)
    assert "beklemeye alındı" in result1
    runtime = _runtime(state)
    assert runtime["status"] == "PAUSED_ERROR"
    assert runtime["completed_iterations"] == 1
    assert runtime["pending_checkpoint"] == 1
    assert lab1._cache_get("iter:1:checkpoint_audit") is None

    second = _agents()
    lab2, trace2, result2 = _run(tmp_path, state, second, "second", checkpoint_every=1)
    assert "Final Bağımsız Audit" in result2
    assert second["auditor"].calls == 2  # checkpoint audit + final audit
    assert lab2._cache_get("iter:1:checkpoint_audit") is not None
    assert [x.title for x in state.list_items(kind="audit") if x.title == "Checkpoint audit 1"]
    runtime = _runtime(state)
    assert runtime["status"] == "COMPLETED"
    assert runtime["pending_checkpoint"] == 0
    assert '"checkpoint_resumed"' in trace2.path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- A05


def test_a05_structured_reasoning_survives_interruption(tmp_path):
    state = ResearchState(tmp_path / "state")
    details = [{"type": "reasoning.encrypted", "data": "opaque-blob"}]
    proposer = FakeAgent("Theorist", "", fail=True, details=details)
    agents = _agents(proposer=proposer)
    lab, _trace, result = _run(tmp_path, state, agents, "interrupted")
    assert "beklemeye alındı" in result
    partial = lab._partial_get("iter:1:proposer")
    assert partial is not None
    assert partial["reasoning_details"] == details


# ---------------------------------------------------------------- A06


def _script_toolbox(tmp_path, body: str) -> tuple[ResearchToolbox, Path]:
    scripts = tmp_path / "research_tools"
    scripts.mkdir(exist_ok=True)
    script = scripts / "checker.py"
    script.write_text(
        "AILAB_ALLOWED_EVIDENCE_KINDS = ('INCONCLUSIVE',)\n"
        "AILAB_ACCEPTS_SPECIFICATION = False\n"
        "AILAB_EVIDENCE_ROLE = 'GENERAL'\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return ResearchToolbox(script_root=scripts, lean_root=tmp_path / "formal", problem_pack_root=None), script


def test_a06_tool_cache_invalidated_when_script_changes(tmp_path):
    state = ResearchState(tmp_path / "state")
    toolbox, script = _script_toolbox(tmp_path, "print('version-one')")
    request = {"tool": "script", "name": "checker.py", "args": []}

    _lab, trace1, result1 = _run(tmp_path, state, _agents(tool_request=request, critic_fail=True), "first", toolbox=toolbox)
    assert "beklemeye alındı" in result1
    assert "version-one" in trace1.path.read_text(encoding="utf-8")

    script.write_text(script.read_text(encoding="utf-8").replace("version-one", "version-two"), encoding="utf-8")
    _lab, trace2, result2 = _run(tmp_path, state, _agents(tool_request=request), "second", toolbox=toolbox)
    text = trace2.path.read_text(encoding="utf-8")
    assert "Final Bağımsız Audit" in result2
    assert '"tool_cache_invalidated"' in text
    assert '"script_changed"' in text
    assert "version-two" in text


def test_a06_unchanged_script_result_is_reused(tmp_path):
    state = ResearchState(tmp_path / "state")
    toolbox, _script = _script_toolbox(tmp_path, "print('version-one')")
    request = {"tool": "script", "name": "checker.py", "args": []}
    _run(tmp_path, state, _agents(tool_request=request, critic_fail=True), "first", toolbox=toolbox)
    _lab, trace2, _result = _run(tmp_path, state, _agents(tool_request=request), "second", toolbox=toolbox)
    text = trace2.path.read_text(encoding="utf-8")
    assert '"tool_cache_invalidated"' not in text
    assert '"step_reused", "step_key": "iter:1:tool"' in text or '"step_key": "iter:1:tool", "tool": "script"' in text


# ---------------------------------------------------------------- A07


def test_a07_large_valid_weights_do_not_create_false_counterexamples():
    circuit = {"n": 2, "gates": [{"id": "g", "op": "edge", "u": 1, "v": 2}], "output": "g"}
    result = TropicalGridTool().check(circuit, weight_values=[0, 10**18 + 1, 10**30])
    assert result.ok is True, result.error or result.output
    assert result.metadata["status"] == "GRID_PASS"
    assert TropicalGridTool._reference(2, {(1, 2): 10**18 + 1}) == 10**18 + 1


# ---------------------------------------------------------------- A08


def test_a08_raw_log_tail_preserves_split_json_records():
    record = json.dumps({"type": "agent_start", "agent": "Theorist", "note": "ünïcödé"}, ensure_ascii=False).encode("utf-8")
    cut = record.index("ü".encode("utf-8")) + 1  # split inside a multi-byte sequence
    state: dict = {"offset": 0, "raw": "", "events": []}

    consume_log_chunk(state, record[:cut], archived=False)
    assert state["events"] == []
    assert state["pending"] == record[:cut]

    consume_log_chunk(state, record[cut:] + b"\n", archived=False)
    assert [e["type"] for e in state["events"]] == ["agent_start"]
    assert state["events"][0]["note"] == "ünïcödé"
    assert state["pending"] == b""

    consume_log_chunk(state, b'{"type": "tail-without-newline"}', archived=True)
    assert [e["type"] for e in state["events"]] == ["agent_start", "tail-without-newline"]


def test_a08_invalid_lines_are_still_reported():
    state: dict = {"offset": 0, "raw": "", "events": []}
    consume_log_chunk(state, b"not json\n", archived=False)
    assert state["events"] == [{"type": "INVALID_JSON", "raw": "not json"}]


def test_fingerprint_helper_still_available():
    assert len(content_fingerprint("x", {"a": 1})) == 64
