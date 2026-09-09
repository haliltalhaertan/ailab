"""Audit reproductions: assertions describe required behavior, so current bugs fail.

Run explicitly; these tests are separate from the repository's existing tests/ suite.
All projects, scripts and locks below are disposable pytest fixtures. No API calls.
"""
from __future__ import annotations

import ast
import gzip
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab.client import LLMClient, LLMResponse, _RequestPolicy
from lab.integrity import ProjectBusyError, ProjectRunLock, atomic_write_json
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused
from lab.theorem_engine import TheoremResearchLab
from lab.tools import LeanTool, ResearchToolbox, TropicalGridTool
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query, limit=8):
        return []


class FakeAgent:
    def __init__(self, name, output, fail=False):
        self.name = name
        self.output = output
        self.fail = fail
        self.calls = 0
        self.model = "audit/fake"
        self.system_prompt = name
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None

    def respond(self, messages, stream_callback=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("audit: checkpoint provider failure")
        return self.output, LLMResponse(self.output, self.model, 1, 1, 0.0)


def agents(auditor_fail=False):
    return {
        "manager": FakeAgent("ResearchManager", '{"status":"OPEN","decision":"KEEP","next_task":"next"}'),
        "proposer": FakeAgent("Theorist", '{"title":"T","claim":"C","tool_request":{"tool":"none"}}'),
        "critic": FakeAgent("AdversarialCritic", '{"verdict":"KEEP","counterexample":""}'),
        "verifier": FakeAgent("VerificationEngineer", '{"verdict":"PASS","counterexample":""}'),
        "auditor": FakeAgent("IndependentAuditor", "audit complete", auditor_fail),
    }


def test_a01_lean_binds_actual_theorem_type(tmp_path):
    tool = LeanTool(tmp_path / "formal")
    draft = tool.draft_source(
        "candidate.lean",
        "def unrelated : Prop := 1 = 2\ntheorem bound : True := True.intro\n",
        theorem_name="bound", theorem_type="1 = 2", item_id="C-audit",
        iteration=1, claim_hash="a" * 64,
    )
    assert not draft.ok, "A theorem of True was accepted as the statement 1 = 2"


def test_a02_stale_lock_reclamation_preserves_new_owner(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    atomic_write_json(root / "run.lock", {"pid": 99999999, "host": socket.gethostname(), "token": "old"})
    first = ProjectRunLock(root)
    second = ProjectRunLock(root)
    original_stale = first._stale

    def interleaved_stale(raw):
        stale = original_stale(raw)
        assert stale
        # Schedule a second worker between first's stale read and its unlink.
        (root / "run.lock").unlink()
        second.acquire()
        return stale

    monkeypatch.setattr(first, "_stale", interleaved_stale)
    try:
        with pytest.raises(ProjectBusyError):
            first.acquire()
    finally:
        first.release()
        second.release()


def test_a03_corrupt_ledger_is_not_silently_replaced(tmp_path):
    state = ResearchState(tmp_path / "project")
    state.add_item("conjecture", "existing", "valuable research")
    damaged = '{"items": ['
    state.state_path.write_text(damaged, encoding="utf-8")
    try:
        state.add_item("conjecture", "new", "new claim")
    except (ValueError, RuntimeError):
        pass
    assert state.state_path.read_text(encoding="utf-8") == damaged, "Unreadable ledger was overwritten by an empty-ledger fallback"


def test_a04_interrupted_checkpoint_is_resumed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = ResearchState(tmp_path / "project")
    first_trace = Trace("audit-first", tmp_path / "runs")
    first = TheoremResearchLab(first_trace, state, literature=EmptyLiterature(), max_retries=1)
    try:
        first.run("problem", iterations=1, checkpoint_every=1, **agents(auditor_fail=True))
        assert first.controller.runtime()["status"] == "PAUSED_ERROR"
        assert first.controller.runtime()["completed_iterations"] == 1
    finally:
        first_trace.close()
    second_trace = Trace("audit-resume", tmp_path / "runs")
    second = TheoremResearchLab(second_trace, state, literature=EmptyLiterature(), max_retries=1)
    try:
        second.run("problem", iterations=1, checkpoint_every=1, **agents())
        assert second.controller.runtime()["status"] == "COMPLETED"
        assert second.step_store.get_step("iter:1:checkpoint_audit") is not None, "Run completed while interrupted checkpoint audit remained missing"
    finally:
        second_trace.close()


def test_a05_structured_reasoning_survives_interruption(tmp_path):
    detail = {"type": "reasoning.encrypted", "data": "opaque-test-data", "index": 0}

    def interrupted_stream():
        yield SimpleNamespace(model="audit/fake", usage=None, choices=[SimpleNamespace(
            finish_reason=None, delta=SimpleNamespace(content="", reasoning="", reasoning_details=[detail]),
        )])
        raise RuntimeError("connection interrupted")

    client = LLMClient.__new__(LLMClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: interrupted_stream())))
    policy = _RequestPolicy(None, None, "provider_default", "unavailable", None, None, "provider_default", None)
    agent = FakeAgent("Theorist", "")

    def respond(messages, stream_callback=None):
        response = client._complete_stream({}, messages, agent.model, policy, stream_callback)
        return response.content, response

    agent.respond = respond
    trace = Trace("audit-stream", tmp_path / "runs")
    lab = TheoremResearchLab(trace, ResearchState(tmp_path / "project"), max_retries=1)
    try:
        with pytest.raises(ResearchPaused):
            lab._call(agent, "test prompt", "iter:1:proposer")
        partial = lab.step_store.get_partial("iter:1:proposer")
        assert partial and partial.get("reasoning_details") == [detail], "Only structured reasoning arrived, and all of it was lost"
    finally:
        trace.close()


def test_a06_tool_cache_invalidated_when_script_changes(tmp_path):
    scripts = tmp_path / "tools"
    scripts.mkdir()
    script = scripts / "check.py"
    script.write_text("print('version-one')\n", encoding="utf-8")
    toolbox = ResearchToolbox(script_root=scripts, lean_root=tmp_path / "formal", problem_pack_root=None)
    trace = Trace("audit-cache", tmp_path / "runs")
    lab = TheoremResearchLab(trace, ResearchState(tmp_path / "project"), toolbox=toolbox)
    request = {"tool": "script", "name": "check.py", "args": []}
    try:
        assert lab._tool(request, "iter:1:tool").output == "version-one"
        script.write_text("print('version-two')\n", encoding="utf-8")
        assert lab._tool(request, "iter:1:tool").output == "version-two", "Changed checker reused old evidence"
    finally:
        trace.close()


def test_a07_large_valid_weights_do_not_create_false_counterexamples():
    circuit = {"n": 2, "gates": [{"id": "e", "op": "edge", "u": 1, "v": 2}], "output": "e"}
    result = TropicalGridTool().check(circuit, weight_values=[10**18 + 1])
    assert result.ok, f"Correct single-edge circuit rejected: {result.metadata}"


def test_a08_raw_log_tail_preserves_split_json_records(tmp_path):
    page = Path(__file__).resolve().parents[3] / "pages" / "2_Ham_Loglar.py"
    tree = ast.parse(page.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_resolved_log_path", "tail_file"}]
    namespace = {"Path": Path, "json": json, "gzip": gzip,
                 "st": SimpleNamespace(session_state={})}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(page), "exec"), namespace)
    path = tmp_path / "trace.jsonl"
    path.write_bytes(b'{"type":"agent_')
    namespace["tail_file"](path, "audit")
    with path.open("ab") as handle:
        handle.write(b'start","agent":"test"}\n')
    _, events = namespace["tail_file"](path, "audit")
    assert events == [{"type": "agent_start", "agent": "test"}], events
