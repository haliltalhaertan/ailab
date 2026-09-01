from lab.client import LLMResponse
from lab.research_state import ResearchState
from lab.resumable_theorem_lab import TheoremResearchLab
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query, limit=8):
        return []


class FakeToolbox:
    def execute(self, request):
        return None


class FakeAgent:
    def __init__(self, name, output, *, fail=False):
        self.name = name
        self.system_prompt = f"system {name}"
        self.model = f"fake/{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.output = output
        self.fail = fail
        self.calls = 0

    def respond(self, messages, stream_callback=None):
        self.calls += 1
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


def make_agents(*, critic_fail=False):
    return {
        "manager": FakeAgent(
            "ResearchManager",
            '{"decision":"KEEP","status":"OPEN","reason":"ok","next_task":"next"}',
        ),
        "proposer": FakeAgent(
            "Theorist",
            '{"title":"T","claim":"C","strategy":"S","evidence_needed":[],"tool_request":{"tool":"none"}}',
        ),
        "critic": FakeAgent(
            "AdversarialCritic",
            '{"verdict":"KEEP","reason":"no counterexample","counterexample":""}',
            fail=critic_fail,
        ),
        "verifier": FakeAgent(
            "VerificationEngineer",
            '{"verdict":"PASS","reason":"ok","formal_proof_required":true,"counterexample":""}',
        ),
        "auditor": FakeAgent("IndependentAuditor", "PASS"),
    }


def run_once(tmp_path, state, agents, experiment):
    trace = Trace(experiment, out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(
        trace,
        state,
        literature=EmptyLiterature(),
        toolbox=FakeToolbox(),
        max_retries=1,
    )
    result = lab.run(
        "frozen problem",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=99,
    )
    trace.close()
    return result


def test_failure_pauses_and_resume_reuses_completed_steps(tmp_path):
    state = ResearchState(tmp_path / "state")
    first = make_agents(critic_fail=True)
    result1 = run_once(tmp_path, state, first, "first")

    assert "beklemeye alındı" in result1
    runtime = __import__("json").loads((state.root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "PAUSED_ERROR"
    assert first["proposer"].calls == 1
    assert first["verifier"].calls == 1
    assert first["critic"].calls == 1
    assert len(state.list_items(kind="conjecture")) == 1

    second = make_agents(critic_fail=False)
    result2 = run_once(tmp_path, state, second, "second")

    assert "Final Bağımsız Audit" in result2
    # proposer/verifier were completed before the crash and must come from cache.
    assert second["proposer"].calls == 0
    assert second["verifier"].calls == 0
    assert second["critic"].calls == 1
    assert second["manager"].calls == 1
    assert len(state.list_items(kind="conjecture")) == 1

    runtime = __import__("json").loads((state.root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "COMPLETED"
    assert runtime["completed_iterations"] == 1
