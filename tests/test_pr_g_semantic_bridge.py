from __future__ import annotations

import json
from pathlib import Path

from lab.agent import Agent
from lab.code_experiment import CodeExperimentRunner, GuardedExperimentWorkspace, WorkspaceActionResult
from lab.integrity import sha256_file
from lab.prompts import manager_prompt, proposal_prompt, verifier_prompt
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.trace import Trace


class NoopClient:
    pass


def _lab(tmp_path: Path):
    trace = Trace("pr-g", out_dir=tmp_path / "runs")
    state = ResearchState(tmp_path / "project")
    lab = TheoremResearchLab(trace, state)
    return lab, trace, state


def _fake_success(workspace: GuardedExperimentWorkspace, text: str = "RAW STDOUT\n"):
    output = workspace.outputs / "fake.stdout.txt"
    output.write_text(text, encoding="utf-8")
    script = workspace.root / "exp_001.py"
    if not script.exists():
        script.write_text("print('x')\n", encoding="utf-8")
    return WorkspaceActionResult(
        True,
        "run_python",
        output=text.strip(),
        metadata={
            "path": "exp_001.py",
            "stdout_file": "outputs/fake.stdout.txt",
            "stdout_sha256": sha256_file(output),
            "script_sha256": sha256_file(script),
            "evidence_level": "COMPUTATION_ONLY",
        },
    )


def test_definitions_written_sha_bound_and_prompt_rules(tmp_path):
    lab, trace, state = _lab(tmp_path)
    item = state.add_item("conjecture", "candidate", "sigma(n) <= 10")
    proposal = {
        "definitions": {
            "sigma": "def sigma(n):\n    return 0 if n == 1 else 1",
            "L": "def L(n):\n    return n + 1",
        }
    }
    request = {"tool": "code_experiment", "task": "check", "source": "print(sigma(1), L(2))"}
    bound = lab._bind_code_definitions(proposal=proposal, item_id=item.id, request=request)
    definitions_path = state.root / "workspace" / "definitions.py"
    assert definitions_path.is_file()
    updated = state.get(item.id)
    assert updated.metadata["definitions_sha256"] == sha256_file(definitions_path)
    assert bound["_definitions_sha256"] == updated.metadata["definitions_sha256"]
    assert bound["_definition_symbols"] == ["sigma", "L"]
    text = proposal_prompt("p", "", "", "task", lab.registry)
    assert "`definitions`" in text
    assert "redefine" in text or "yeniden" in text
    trace.close()


def test_invalid_definition_policy_is_rejected(tmp_path):
    lab, trace, state = _lab(tmp_path)
    item = state.add_item("conjecture", "candidate", "claim")
    bound = lab._bind_code_definitions(
        proposal={"definitions": {"sigma": "import os\ndef sigma(n):\n    return n"}},
        item_id=item.id,
        request={"tool": "code_experiment", "task": "check"},
    )
    assert "_definitions_error" in bound
    assert not (state.root / "workspace" / "definitions.py").exists()
    trace.close()


def test_theorist_source_written_before_agent_and_cannot_be_overwritten(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    trace = Trace("source-first", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=4)
    workspace.write_file("definitions.py", "def sigma(n):\n    return n + 1\n")
    timeline = []
    responses = iter(
        [
            json.dumps({"action": "write_file", "path": "exp_001.py", "content": "print('WRONG')"}),
            json.dumps({"action": "run_python", "path": "exp_001.py", "args": []}),
            json.dumps({"action": "finish", "summary": "agent says something else"}),
        ]
    )

    def call_agent(_agent, prompt, _step):
        timeline.append(("agent", prompt))
        return next(responses)

    def execute(_key, action):
        timeline.append(("execute", dict(action)))
        if action["action"] == "run_python":
            return _fake_success(workspace, "RAW\n")
        return workspace.execute(action)

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "", model="fake/model", client=NoopClient()),
        task="run requested check",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute,
        source="print(sigma(1))",
        definitions_file="definitions.py",
        definition_symbols=["sigma"],
    )
    assert timeline[0][0] == "execute"
    assert timeline[0][1]["action"] == "write_file"
    assert timeline[0][1]["content"] == "from definitions import sigma\n\nprint(sigma(1))"
    assert workspace.read_file("exp_001.py").output == "from definitions import sigma\n\nprint(sigma(1))"
    assert "CANONICAL DEFINITIONS" in timeline[1][1]
    assert "yeniden tanımlama" in timeline[1][1]
    assert result.ok is True
    assert result.output == "RAW\n"
    trace.close()


def test_prewritten_source_patch_is_allowed_within_budget(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    trace = Trace("patch", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=3)
    source = "x = 1\n" + ("# pad\n" * 20) + "print(x)\n"
    responses = iter(
        [
            json.dumps({"action": "patch_file", "path": "exp_001.py", "old": "x = 1", "new": "x = 2"}),
            json.dumps({"action": "run_python", "path": "exp_001.py", "args": []}),
            json.dumps({"action": "finish", "summary": "done"}),
        ]
    )

    def call_agent(_agent, _prompt, _step):
        return next(responses)

    def execute(_key, action):
        if action["action"] == "run_python":
            return _fake_success(workspace, "2\n")
        return workspace.execute(action)

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "", model="fake/model", client=NoopClient()),
        task="check",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute,
        source=source,
    )
    assert result.ok
    assert "x = 2" in workspace.read_file("exp_001.py").output
    trace.close()


def test_finish_summary_is_not_evidence_and_stdout_sha_matches(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    trace = Trace("stdout", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=2)
    raw_stdout = 'sigma_total(60)=19\nAILAB_RESULT={"sigma":19,"n":60}\n'
    responses = iter(
        [
            json.dumps({"action": "run_python", "path": "exp_001.py", "args": []}),
            json.dumps({"action": "finish", "summary": "n=60: sigma=168"}),
        ]
    )
    workspace.write_file("exp_001.py", "print('placeholder')\n")

    def call_agent(_agent, _prompt, _step):
        return next(responses)

    def execute(_key, action):
        if action["action"] == "run_python":
            return _fake_success(workspace, raw_stdout)
        return workspace.execute(action)

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "", model="fake/model", client=NoopClient()),
        task="check",
        step_key="iter:3:tool",
        call_agent=call_agent,
        execute_cached=execute,
    )
    assert result.ok
    assert result.output == raw_stdout
    assert result.metadata["agent_summary"] == "n=60: sigma=168"
    stdout_path = workspace.root / result.metadata["stdout_file"]
    assert result.metadata["stdout_sha256"] == sha256_file(stdout_path)
    assert result.metadata["structured_trailer"]["AILAB_RESULT"] == {"sigma": 19, "n": 60}
    verifier = verifier_prompt("p", "C-1", {}, result.as_dict())
    assert "canonical raw stdout evidence" in verifier
    assert "NOT evidence" in verifier
    trace.close()


def test_step_limit_with_successful_stdout_is_usable_evidence(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    workspace.write_file("exp_001.py", "print('ok')\n")
    trace = Trace("limit", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=1)

    def call_agent(_agent, _prompt, _step):
        return json.dumps({"action": "run_python", "path": "exp_001.py", "args": []})

    def execute(_key, action):
        return _fake_success(workspace, "RESULT=1\n")

    result = runner.run(
        agent=Agent("CodeExperimentAgent", "", model="fake/model", client=NoopClient()),
        task="check",
        step_key="iter:2:tool",
        call_agent=call_agent,
        execute_cached=execute,
    )
    assert result.ok is True
    assert result.output == "RESULT=1\n"
    assert result.metadata["status"] == "STEP_LIMIT_WITH_OUTPUT"
    assert result.error == ""
    trace.close()


def test_container_launcher_preserves_isolation_and_allows_workspace_definitions(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    workspace.write_file("definitions.py", "def sigma(n):\n    return n + 1\n")
    workspace.write_file("exp_001.py", "from definitions import sigma\nprint(sigma(1))\n")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    monkeypatch.setattr(workspace, "_kill_container", lambda _name: None)
    captured = {}

    class Proc:
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

        def kill(self):
            self.returncode = -9

    def fake_popen(command, **kwargs):
        del kwargs
        captured["command"] = list(command)
        return Proc()

    monkeypatch.setattr("lab.code_experiment.subprocess.Popen", fake_popen)
    result = workspace.run_python("exp_001.py")
    assert result.ok
    command = captured["command"]
    python_index = command.index("python")
    assert command[python_index + 1 : python_index + 3] == ["-I", "-c"]
    launcher = command[python_index + 3]
    assert "sys.path.insert(0" in launcher
    assert "/workspace" in launcher
    assert 'run_name="__main__"' in launcher
    assert command[python_index + 4] == "/workspace/exp_001.py"


def test_two_turn_repeated_next_task_emits_warning_and_manager_prompt(tmp_path):
    lab, trace, state = _lab(tmp_path)
    state.add_item(
        "conjecture",
        "previous",
        "claim",
        metadata={
            "target_id": "T1",
            "input_next_task": "sigma toplam durma süresini aynı sınıflarda test et",
            "manager_decision": "REVISE",
            "manager_next_task": "sigma toplam durma süresini aynı sınıflarda yeniden test et",
        },
    )
    current = state.add_item("conjecture", "current", "claim2", metadata={"target_id": "T1"})
    warning = lab._repeated_next_task_warning(
        current_item_id=current.id,
        current_task="sigma toplam durma süresini aynı sınıflarda tekrar test et",
        target_id="T1",
    )
    assert "iki turdur tekrarlıyor" in warning
    prompt = manager_prompt(
        "p",
        current.id,
        current.claim,
        None,
        {},
        {},
        repeat_warning=warning,
    )
    assert "ya farklı bir aday iste ya da DROPPED öner" in prompt
    trace.close()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert any(event.get("type") == "repeated_next_task" for event in events)
