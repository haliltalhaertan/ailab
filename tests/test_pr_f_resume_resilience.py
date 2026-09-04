
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from lab.agent import Agent
from lab.client import LLMResponse
from lab.code_experiment import CodeExperimentRunner, GuardedExperimentWorkspace, WorkspaceActionResult
from lab.integrity import content_fingerprint, sha256_file
from lab.iteration_control import restart_iteration
from lab.prompts import proposal_prompt
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused, retryable
from lab.theorem_engine import TheoremResearchLab
from lab.trace import Trace
from lab.worker import _tool_availability_for_run


class FlakyClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        del messages, kwargs
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "https://example.test")
            raise httpx.ReadError(
                "[WinError 10054] Eine vorhandene Verbindung wurde vom Remotehost geschlossen",
                request=request,
            )
        return LLMResponse("ok", "fake/model", 1, 1, 0.01, finish_reason="stop")


def _lab(tmp_path: Path, *, max_retries: int = 3):
    trace = Trace("pr-f", out_dir=tmp_path / "runs")
    state = ResearchState(tmp_path / "project")
    lab = TheoremResearchLab(trace, state, max_retries=max_retries)
    return lab, trace, state


def test_retryable_uses_types_and_windows_codes_not_locale():
    request = httpx.Request("POST", "https://example.test")
    assert retryable(httpx.ReadError("Eine vorhandene Verbindung wurde geschlossen", request=request))
    assert retryable(httpx.ReadError("connection reset by peer", request=request))
    win = OSError("Eine vorhandene Verbindung wurde vom Remotehost geschlossen")
    win.winerror = 10054
    assert retryable(win)
    assert retryable(ConnectionResetError("reset"))
    assert not retryable(ValueError("bad user input"))


def test_agent_read_error_retries_then_succeeds(tmp_path, monkeypatch):
    lab, trace, _state = _lab(tmp_path)
    monkeypatch.setattr("lab.theorem_engine.time.sleep", lambda _seconds: None)
    client = FlakyClient()
    agent = Agent("Theorist", "system", model="fake/model", client=client)
    assert lab._call(agent, "prompt", "iter:1:proposer") == "ok"
    assert client.calls == 2
    trace.close()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert sum(event.get("type") == "agent_retry" for event in events) == 1


def test_bound_iteration_proposal_is_canonical_after_prompt_change(tmp_path, monkeypatch):
    lab, trace, state = _lab(tmp_path)
    proposal = {"title": "old", "claim": "C", "strategy": "s", "tool_request": {"tool": "none"}}
    proposal_hash = content_fingerprint("proposal:v1", proposal)
    item = state.add_item(
        "conjecture",
        "old",
        "C",
        metadata={"iteration": 2, "proposal": proposal, "proposal_hash": proposal_hash},
    )
    lab.step_store.put_iteration_snapshot(
        2,
        ledger_revision="frozen",
        ledger_context="ctx",
        payload={"proposal": proposal, "proposal_hash": proposal_hash, "item_id": item.id},
    )
    calls = {"count": 0}

    def forbidden(*_args, **_kwargs):
        calls["count"] += 1
        raise AssertionError("Theorist must not be called for a bound iteration")

    monkeypatch.setattr(lab, "_call_json", forbidden)
    snapshot = lab.step_store.get_iteration_snapshot(2)
    reused, bound = lab._proposal_for_iteration(
        2, snapshot, Agent("Theorist", "new prompt"), "changed prompt", "iter:2:proposer"
    )
    assert reused == proposal
    assert bound is not None and bound.id == item.id
    assert calls["count"] == 0
    trace.close()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event.get("type") == "proposal_reused_from_ledger" and event.get("item_id") == item.id
        for event in events
    )


def test_bound_item_missing_snapshot_proposal_pauses_actionably(tmp_path):
    lab, trace, state = _lab(tmp_path)
    proposal = {"title": "old", "claim": "C"}
    proposal_hash = content_fingerprint("proposal:v1", proposal)
    state.add_item(
        "conjecture",
        "old",
        "C",
        metadata={"iteration": 2, "proposal_hash": proposal_hash},
    )
    lab.step_store.put_iteration_snapshot(
        2, ledger_revision="frozen", ledger_context="ctx", payload={}
    )
    snapshot = lab.step_store.get_iteration_snapshot(2)
    with pytest.raises(ResearchPaused, match="Tur 2.*yeniden başlat"):
        lab._proposal_for_iteration(
            2, snapshot, Agent("Theorist", "x"), "prompt", "iter:2:proposer"
        )
    trace.close()


def test_iteration_restart_drops_item_preserves_evidence_and_allows_new_proposal(tmp_path, monkeypatch):
    lab, trace, state = _lab(tmp_path)
    proposal = {"title": "old", "claim": "C", "strategy": "s"}
    proposal_hash = content_fingerprint("proposal:v1", proposal)
    item = state.add_item(
        "conjecture",
        "old",
        "C",
        evidence=["keep-me"],
        metadata={"iteration": 2, "proposal": proposal, "proposal_hash": proposal_hash},
    )
    lab.step_store.put_iteration_snapshot(
        2,
        ledger_revision="frozen",
        ledger_context="ctx",
        payload={"proposal": proposal, "proposal_hash": proposal_hash, "item_id": item.id},
    )
    lab.step_store.put_step(
        "iter:2:proposer", {"status": "COMPLETE", "fingerprint": "x", "content": "old"}
    )
    detail = restart_iteration(state.root, 2)
    dropped = state.get(item.id)
    assert dropped.status == "DROPPED"
    assert dropped.evidence == ["keep-me"]
    assert dropped.metadata["superseded_reason"] == "iteration_restart"
    assert detail["cleared"]["snapshots"] == 1
    assert lab.step_store.get_iteration_snapshot(2) is None
    assert lab.step_store.get_step("iter:2:proposer") is None

    fresh_snapshot = lab._iteration_snapshot(2, "next")
    calls = {"count": 0}
    fresh = {"title": "new", "claim": "D", "strategy": "fresh"}

    def fake_call(*_args, **_kwargs):
        calls["count"] += 1
        return dict(fresh)

    monkeypatch.setattr(lab, "_call_json", fake_call)
    proposal2, bound = lab._proposal_for_iteration(
        2, fresh_snapshot, Agent("Theorist", "x"), "prompt", "iter:2:proposer"
    )
    assert proposal2 == fresh
    assert bound is None
    assert calls["count"] == 1
    new_item = lab._ensure_item_matches_proposal(2, proposal2, fresh_snapshot)
    assert new_item.id != item.id
    trace.close()


def test_daemon_probe_closes_capability_and_prompt_when_cli_exists_but_daemon_down(tmp_path, monkeypatch):
    import lab.code_experiment as ce

    monkeypatch.setattr(ce.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    monkeypatch.setattr(
        ce.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="failed to connect to the docker API; daemon is running?"
        ),
    )
    lab, trace, state = _lab(tmp_path)
    lab.code_workspace.container_engine = "docker"
    lab.code_workspace.refresh_execution_availability()
    assert lab.code_workspace.execution_available is False
    assert "daemon erişilemiyor" in lab.code_workspace.availability_reason

    snapshot = _tool_availability_for_run(state.root, lab, {"status": "NEW"})
    row = snapshot["effective_tool_availability"]["code_experiment"]
    assert row["available"] is False
    assert "daemon erişilemiyor" in row["reason"]
    prompt = proposal_prompt("P", "L", "ledger", "task", lab.registry)
    assert "code_experiment: KAPALI" in prompt
    trace.close()


def test_infrastructure_failure_stops_code_agent_after_one_plan(tmp_path, monkeypatch):
    import lab.code_experiment as ce

    monkeypatch.setattr(ce.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    monkeypatch.setattr(
        ce.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Server Version: 27.0.0", stderr=""
        ),
    )
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace", container_engine="docker")
    trace = Trace("infra", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=6)
    workspace.write_file("exp.py", "print(1)\n")
    calls = {"count": 0}

    def call_agent(*_args, **_kwargs):
        calls["count"] += 1
        return '{"action":"run_python","path":"exp.py","args":[]}'

    def execute_cached(_key, _action):
        return WorkspaceActionResult(
            False,
            "run_python",
            error="infrastructure: failed to connect to the docker API",
            metadata={"infrastructure_error": True, "tool_unavailable": True},
        )

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "x"),
        task="t",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute_cached,
    )
    assert not result.ok
    assert result.metadata["infrastructure_error"] is True
    assert result.metadata["tool_unavailable"] is True
    assert calls["count"] == 1
    trace.close()


def test_identical_failed_action_twice_breaks_loop(tmp_path, monkeypatch):
    import lab.code_experiment as ce

    monkeypatch.setattr(ce.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    monkeypatch.setattr(
        ce.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Server Version: 27.0.0", stderr=""
        ),
    )
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace", container_engine="docker")
    trace = Trace("repeat", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=6)
    calls = {"count": 0}

    def call_agent(*_args, **_kwargs):
        calls["count"] += 1
        return '{"action":"read_file","path":"missing.txt"}'

    def execute_cached(_key, _action):
        return WorkspaceActionResult(False, "read_file", error="missing")

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "x"),
        task="t",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute_cached,
    )
    assert not result.ok
    assert result.metadata["status"] == "REPEATED_FAILURE"
    assert calls["count"] == 2
    trace.close()


def test_infra_failed_workspace_action_is_not_reused_after_recovery(tmp_path, monkeypatch):
    lab, trace, _state = _lab(tmp_path)
    action = {"action": "run_python", "path": "exp.py", "args": []}
    lab.code_workspace.write_file("exp.py", "print(1)\n")
    calls = {"count": 0}

    def fake_execute(_action):
        calls["count"] += 1
        if calls["count"] == 1:
            return WorkspaceActionResult(
                False,
                "run_python",
                error="infrastructure: daemon down",
                metadata={"infrastructure_error": True, "tool_unavailable": True},
            )
        return WorkspaceActionResult(
            True,
            "run_python",
            output="1",
            metadata={
                "path": "exp.py",
                "script_sha256": sha256_file(lab.code_workspace.root / "exp.py"),
            },
        )

    monkeypatch.setattr(lab.code_workspace, "execute", fake_execute)
    first = lab._cached_workspace_action("iter:1:tool:action:1", action)
    second = lab._cached_workspace_action("iter:1:tool:action:1", action)
    assert not first.ok and second.ok
    assert calls["count"] == 2
    trace.close()


def test_legacy_complete_infrastructure_cache_is_invalidated(tmp_path, monkeypatch):
    lab, trace, _state = _lab(tmp_path)
    action = {"action": "run_python", "path": "exp.py", "args": []}
    lab.code_workspace.write_file("exp.py", "print(1)\n")
    fingerprint = content_fingerprint("code_experiment_action:v3", action)
    lab.step_store.put_step(
        "iter:1:tool:action:1",
        {
            "status": "COMPLETE",
            "fingerprint": fingerprint,
            "result": WorkspaceActionResult(
                False,
                "run_python",
                error="failed to connect to the docker API at npipe:////./pipe/docker_engine; daemon is running?",
                metadata={},
            ).as_dict(),
        },
    )
    calls = {"count": 0}

    def recovered(_action):
        calls["count"] += 1
        return WorkspaceActionResult(
            True,
            "run_python",
            output="ok",
            metadata={
                "path": "exp.py",
                "script_sha256": sha256_file(lab.code_workspace.root / "exp.py"),
            },
        )

    monkeypatch.setattr(lab.code_workspace, "execute", recovered)
    result = lab._cached_workspace_action("iter:1:tool:action:1", action)
    assert result.ok
    assert calls["count"] == 1
    trace.close()



def test_code_experiment_profile_defaults_to_low_reasoning():
    from lab.ui_model import load_default_agent_profile

    profile = load_default_agent_profile()
    raw = profile["agents"]["CodeExperimentAgent"]
    assert raw["reasoning_effort"] == "low"
    assert raw["model"] == profile["orchestrator_default"]["model"]


def test_app_default_reasoning_settings_and_worker_request_include_code_agent():
    source = Path("app.py").read_text(encoding="utf-8")
    assert 'roles.append("CodeExperimentAgent")' in source
    assert 'get_reasoning_effort("CodeExperimentAgent")' in source
    assert 'request["code_experiment"] = code_experiment' in source


def test_engine_created_code_agent_uses_low_reasoning(tmp_path, monkeypatch):
    lab, trace, _state = _lab(tmp_path)
    monkeypatch.setattr("lab.theorem_engine.get_reasoning_effort", lambda _name: "low")
    captured = {}

    def fake_run_inner(*_args, **_kwargs):
        captured["effort"] = lab.code_agent.reasoning_effort if lab.code_agent is not None else None
        return "ok"

    monkeypatch.setattr(lab, "_run_inner", fake_run_inner)
    base = Agent("base", "system", model="fake/model")
    result = lab.run(
        "P",
        manager=Agent("ResearchManager", "system", model="fake/model"),
        proposer=Agent("Theorist", "system", model="fake/model"),
        critic=Agent("AdversarialCritic", "system", model="fake/model"),
        verifier=Agent("VerificationEngineer", "system", model="fake/model"),
        auditor=Agent("IndependentAuditor", "system", model="fake/model"),
        iterations=1,
    )
    del base
    assert result == "ok"
    assert captured["effort"] == "low"
    assert lab.code_settings["reasoning_effort"] == "low"
    trace.close()


def test_code_experiment_prompt_is_implementer_not_theorist():
    from lab.code_experiment import CODE_EXPERIMENT_SYSTEM_PROMPT

    assert "Sen uygulayıcısın" in CODE_EXPERIMENT_SYSTEM_PROMPT
    assert "yeni teori geliştirme yapma" in CODE_EXPERIMENT_SYSTEM_PROMPT
    assert "İlk eylemin deney scriptini write_file" in CODE_EXPERIMENT_SYSTEM_PROMPT


def test_code_action_plaintext_gets_one_repair_then_continues(tmp_path, monkeypatch):
    import lab.code_experiment as ce

    monkeypatch.setattr(ce.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    monkeypatch.setattr(
        ce.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Server Version: 27.0.0", stderr=""
        ),
    )
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace-repair", container_engine="docker")
    trace = Trace("action-repair", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=3)
    calls = []

    def call_agent(_agent, _prompt, key):
        calls.append(key)
        if key.endswith("plan:1"):
            return "Deneyi tasarlıyorum; önce birkaç matematiksel fikir düşünelim."
        if key.endswith("action_repair"):
            return '{"action":"write_file","path":"exp.py","content":"print(1)\\n"}'
        if key.endswith("plan:2"):
            return '{"action":"run_python","path":"exp.py","args":[]}'
        return '{"action":"finish","summary":"ok"}'

    def execute_cached(_key, action):
        if action.get("action") == "write_file":
            return workspace.write_file(str(action["path"]), str(action["content"]))
        if action.get("action") == "run_python":
            return WorkspaceActionResult(True, "run_python", output="1", metadata={"evidence_level": "COMPUTATION_ONLY"})
        raise AssertionError(action)

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "x", reasoning_effort="low"),
        task="hesabı kodla",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute_cached,
    )
    assert result.ok
    assert sum(key.endswith("action_repair") for key in calls) == 1
    assert any(key.endswith("plan:2") for key in calls)
    trace.close()


def test_code_action_second_format_failure_returns_tool_result_not_pause(tmp_path, monkeypatch):
    import lab.code_experiment as ce

    monkeypatch.setattr(ce.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    monkeypatch.setattr(
        ce.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Server Version: 27.0.0", stderr=""
        ),
    )
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace-format", container_engine="docker")
    trace = Trace("action-format", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=4)
    calls = {"count": 0}

    def call_agent(*_args, **_kwargs):
        calls["count"] += 1
        return "düz yazı; JSON yok"

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "x", reasoning_effort="low"),
        task="hesabı kodla",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=lambda *_args: (_ for _ in ()).throw(AssertionError("action must not execute")),
    )
    assert not result.ok
    assert result.metadata["status"] == "ACTION_FORMAT_ERROR"
    assert result.metadata["format_error"] is True
    assert "agent eylem üretemedi" in result.error
    assert calls["count"] == 2
    trace.close()


def test_provider_default_reasoning_effort_is_visible_on_card():
    from lab.ui_live import build_cards

    cards = build_cards(
        [
            {
                "type": "agent_start",
                "agent": "CodeExperimentAgent",
                "model": "z-ai/glm-5.3-flash",
                "step_key": "iter:1:tool:plan:1",
                "reasoning_effort": None,
            },
            {
                "type": "llm_call",
                "agent": "CodeExperimentAgent",
                "model": "z-ai/glm-5.3-flash",
                "step_key": "iter:1:tool:plan:1",
                "reasoning_effort_requested": None,
                "reasoning_effort_sent": None,
                "effort_resolution": "provider_default",
                "model_default_reasoning_effort": "max",
            },
        ]
    )
    assert len(cards) == 1
    assert cards[0].reasoning_effort_sent is None
    assert cards[0].effort_resolution == "provider_default"
    assert cards[0].model_default_reasoning_effort == "max"
