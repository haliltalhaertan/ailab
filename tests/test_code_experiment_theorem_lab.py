from lab.client import LLMResponse
from lab.code_experiment import CODE_EXPERIMENT_SYSTEM_PROMPT
from lab.code_experiment_theorem_lab import TheoremResearchLab
from lab.research_state import ResearchState
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query, limit=8):
        return []


class FakeCodeAgent:
    def __init__(self):
        self.name = "CodeExperimentAgent"
        self.system_prompt = CODE_EXPERIMENT_SYSTEM_PROMPT
        self.model = "fake/code"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.calls = 0
        self.outputs = [
            '{"action":"write_file","path":"exp_001.py","content":"print(sum(range(5)))\\n"}',
            '{"action":"run_python","path":"exp_001.py","args":[]}',
            '{"action":"finish","summary":"sum(range(5)) = 10 doğrulandı"}',
        ]

    def respond(self, messages, stream_callback=None):
        output = self.outputs[self.calls]
        self.calls += 1
        if stream_callback:
            stream_callback("content", output)
        return output, LLMResponse(
            content=output,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=10,
            latency_s=0.01,
            cost_usd=0.001,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )


def test_code_experiment_tool_writes_runs_and_finishes(tmp_path, fake_container_runtime):
    state = ResearchState(tmp_path / "state")
    trace = Trace("code-exp", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), code_experiment_steps=5)
    agent = FakeCodeAgent()
    lab.code_agent = agent

    result = lab._tool(
        {"tool": "code_experiment", "task": "Basit deterministic smoke test yap."},
        "iter:1:tool",
    )
    trace.close()

    assert result is not None and result.ok
    assert result.tool == "code_experiment"
    assert result.metadata["evidence_level"] == "COMPUTATION_ONLY"
    assert agent.calls == 3
    assert fake_container_runtime
    assert (state.root / "workspace" / "exp_001.py").exists()
    stdout_files = list((state.root / "workspace" / "outputs").glob("*.stdout.txt"))
    assert stdout_files
    assert stdout_files[0].read_text(encoding="utf-8").strip() == "10"


def test_completed_code_experiment_is_reused(tmp_path, fake_container_runtime):
    state = ResearchState(tmp_path / "state")
    first_trace = Trace("first", out_dir=tmp_path / "runs")
    first_lab = TheoremResearchLab(first_trace, state, literature=EmptyLiterature(), code_experiment_steps=5)
    first_agent = FakeCodeAgent()
    first_lab.code_agent = first_agent
    first_lab._tool({"tool": "code_experiment", "task": "test"}, "iter:1:tool")
    first_trace.close()

    first_command_count = len(fake_container_runtime)
    second_trace = Trace("second", out_dir=tmp_path / "runs")
    second_lab = TheoremResearchLab(second_trace, state, literature=EmptyLiterature(), code_experiment_steps=5)
    second_agent = FakeCodeAgent()
    second_lab.code_agent = second_agent
    result = second_lab._tool({"tool": "code_experiment", "task": "test"}, "iter:1:tool")
    second_trace.close()

    assert result is not None and result.ok
    assert second_agent.calls == 0
    assert len(fake_container_runtime) == first_command_count
