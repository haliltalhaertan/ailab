import json
from types import SimpleNamespace

import pytest

from lab import ProjectBusyError, ProjectRunLock, ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.code_experiment import CodeExperimentRunner, GuardedExperimentWorkspace
from lab.literature import Paper
from lab.project_manager import ProjectManager


class EmptyToolbox:
    def execute(self, request):
        return None


class CountingLiterature:
    def __init__(self):
        self.calls = []

    def search(self, query, limit=8):
        self.calls.append((query, limit))
        return [
            Paper(
                title=f"paper:{query}",
                authors=["A"],
                year=2026,
                url="https://example.test/paper",
                source="test",
            )
        ]


class FakeAgent:
    def __init__(self, outputs=None, *, name="Theorist", effort="xhigh"):
        self.name = name
        self.system_prompt = "system-v1"
        self.model = "fake/model"
        self.temperature = 0.1
        self.max_tokens = 1234
        self.reasoning_effort = effort
        self.client = SimpleNamespace(base_url="https://openrouter.ai/api/v1")
        self.outputs = list(outputs or ["ok"])
        self.calls = 0
        self.seen_messages = []

    def respond(self, messages, stream_callback=None):
        self.seen_messages.append(messages)
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if stream_callback:
            stream_callback("content", output)
        return output, LLMResponse(
            content=output,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_s=0.01,
            cost_usd=0.001,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
            requested_reasoning_effort=self.reasoning_effort,
        )


class DetailsInterruptAgent(FakeAgent):
    def respond(self, messages, stream_callback=None):
        self.seen_messages.append(messages)
        self.calls += 1
        if self.calls == 1:
            if stream_callback:
                stream_callback("reasoning", "first reasoning fragment")
                stream_callback(
                    "reasoning_details",
                    [{"type": "reasoning.text", "id": "r1", "text": "structured fragment"}],
                )
                stream_callback("content", "partial answer")
            raise RuntimeError("connection reset by peer")
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert messages[1]["reasoning_details"][0]["id"] == "r1"
        assert "partial answer" in messages[1]["content"]
        output = "complete answer"
        if stream_callback:
            stream_callback("content", output)
        return output, LLMResponse(
            content=output,
            model=self.model,
            prompt_tokens=20,
            completion_tokens=8,
            latency_s=0.01,
            cost_usd=0.002,
            reasoning_details=[{"type": "reasoning.text", "id": "r1", "text": "structured fragment"}],
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
            requested_reasoning_effort=self.reasoning_effort,
        )


def make_lab(tmp_path, *, literature=None, retries=2):
    state = ResearchState(tmp_path / "state")
    trace = Trace("hardening", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(
        trace,
        state,
        literature=literature or CountingLiterature(),
        toolbox=EmptyToolbox(),
        max_retries=retries,
    )
    return lab, state, trace


def test_concurrent_same_project_is_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    first = ProjectRunLock(root).acquire()
    try:
        with pytest.raises(ProjectBusyError, match="başka bir process"):
            ProjectRunLock(root).acquire()
    finally:
        first.release()
    assert not (root / "run.lock").exists()


def test_trace_ids_never_collide(tmp_path):
    a = Trace("theorem", out_dir=tmp_path / "runs")
    b = Trace("theorem", out_dir=tmp_path / "runs")
    try:
        assert a.run_dir != b.run_dir
        assert a.run_id != b.run_id
    finally:
        a.close()
        b.close()


def test_code_experiment_finish_without_run_is_rejected(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace", timeout_s=5)
    trace = Trace("finish-gate", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(ws, trace, max_steps=1)
    agent = FakeAgent(name="CodeExperimentAgent")

    def call_agent(agent, prompt, step_key):
        return '{"action":"finish","summary":"pretend success"}'

    result = runner.run(
        agent=agent,
        task="test",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=lambda key, action: ws.execute(action),
    )
    trace.close()
    assert not result.ok
    assert result.metadata["successful_run_count"] == 0


def test_code_experiment_finish_after_failed_run_is_rejected(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace", timeout_s=5)
    trace = Trace("failed-gate", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(ws, trace, max_steps=3)
    agent = FakeAgent(name="CodeExperimentAgent")
    outputs = iter(
        [
            '{"action":"write_file","path":"bad.py","content":"raise RuntimeError(\\"boom\\")\\n"}',
            '{"action":"run_python","path":"bad.py","args":[]}',
            '{"action":"finish","summary":"pretend success"}',
        ]
    )

    result = runner.run(
        agent=agent,
        task="test",
        step_key="iter:1:tool",
        call_agent=lambda agent, prompt, step_key: next(outputs),
        execute_cached=lambda key, action: ws.execute(action),
    )
    trace.close()
    assert not result.ok
    assert result.metadata["successful_run_count"] == 0
    assert result.metadata["failed_run_count"] == 1


def test_experiment_outputs_are_never_overwritten(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace", timeout_s=5)
    assert ws.write_file("exp.py", "print('same')\n").ok
    first = ws.run_python("exp.py")
    second = ws.run_python("exp.py")
    assert first.ok and second.ok
    assert first.metadata["stdout_file"] != second.metadata["stdout_file"]
    assert (ws.root / first.metadata["stdout_file"]).read_text(encoding="utf-8").strip() == "same"
    assert (ws.root / second.metadata["stdout_file"]).read_text(encoding="utf-8").strip() == "same"
    assert first.metadata["stdout_sha256"] == second.metadata["stdout_sha256"]


def test_literature_cache_changes_when_query_changes(tmp_path):
    literature = CountingLiterature()
    lab, state, trace = make_lab(tmp_path, literature=literature)
    lab._search_literature("query one", limit=3)
    lab._search_literature("query one", limit=3)
    lab._search_literature("query two", limit=3)
    trace.close()
    assert literature.calls == [("query one", 3), ("query two", 3)]


def test_llm_cache_fingerprint_changes_with_prompt(tmp_path):
    lab, state, trace = make_lab(tmp_path)
    agent = FakeAgent(outputs=["answer-one", "answer-two"])
    first = lab._call(agent, "prompt one", "iter:1:proposer")
    second = lab._call(agent, "prompt two", "iter:1:proposer")
    trace.close()
    assert first == "answer-one"
    assert second == "answer-two"
    assert agent.calls == 2


def test_reasoning_details_survive_resume(tmp_path):
    lab, state, trace = make_lab(tmp_path, retries=2)
    agent = DetailsInterruptAgent()
    result = lab._call(agent, "solve", "iter:1:proposer")
    trace.close()
    assert result == "complete answer"
    assert agent.calls == 2
    partials = json.loads((state.root / "partial_steps.json").read_text(encoding="utf-8"))
    assert "iter:1:proposer" not in partials


def test_reasoning_effort_is_persisted_in_run_config(tmp_path):
    lab, state, trace = make_lab(tmp_path)
    agent = FakeAgent(effort="xhigh")
    lab._save_config(
        "problem",
        4,
        "literature query",
        2,
        {"Theorist": agent},
    )
    config = json.loads((state.root / "run_config.json").read_text(encoding="utf-8"))
    trace.close()
    assert config["agents"]["Theorist"]["reasoning_effort"] == "xhigh"
    assert config["config_version"] == 2


def test_delete_and_recreate_project_does_not_inherit_history(tmp_path):
    state_root = tmp_path / "research_state"
    runs_root = tmp_path / "runs"
    pm = ProjectManager(state_root, runs_root)
    first = pm.create_project(title="First", project_id="same", problem="p", activate=False)
    old_run = runs_root / "old_theorem"
    old_run.mkdir(parents=True)
    (old_run / "trace.jsonl").write_text(
        json.dumps(
            {
                "ts": first.created_at,
                "type": "project_context",
                "project_id": "same",
                "project_uuid": first.project_uuid,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (old_run / "summary.json").write_text(
        json.dumps({"started_at": first.created_at, "total_tokens": 999, "total_cost_usd": 1.0}),
        encoding="utf-8",
    )
    assert pm.get("same").run_count == 1

    pm.delete("same")
    second = pm.create_project(title="Second", project_id="same", problem="new", activate=False)
    assert second.project_uuid != first.project_uuid
    assert pm.get("same").run_count == 0
    assert pm.get("same").total_tokens == 0
