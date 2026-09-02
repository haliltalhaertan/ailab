from lab.client import LLMResponse
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query, limit=8):
        return []


class FakeToolbox:
    def execute(self, request):
        return None


class InterruptThenFinishAgent:
    def __init__(self):
        self.name = "Theorist"
        self.system_prompt = "system"
        self.model = "fake/model"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.calls = 0
        self.seen_messages = []

    def respond(self, messages, stream_callback=None):
        self.calls += 1
        self.seen_messages.append(messages)
        if self.calls == 1:
            if stream_callback:
                stream_callback("reasoning", "use lemma A; ")
                stream_callback("content", "partial answer")
            raise RuntimeError("connection reset by peer")
        assert "SOFT RESUME CONTEXT" in messages[0]["content"]
        assert "use lemma A" in messages[0]["content"]
        assert "partial answer" in messages[0]["content"]
        output = "complete final answer"
        if stream_callback:
            stream_callback("content", output)
        return output, LLMResponse(
            content=output,
            model=self.model,
            prompt_tokens=20,
            completion_tokens=5,
            latency_s=0.01,
            cost_usd=0.001,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )


class PartialThen404Agent:
    def __init__(self, *, fail=True):
        self.name = "AdversarialCritic"
        self.system_prompt = "system"
        self.model = "fake/critic"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.fail = fail
        self.calls = 0
        self.seen_messages = []

    def respond(self, messages, stream_callback=None):
        self.calls += 1
        self.seen_messages.append(messages)
        if self.fail:
            if stream_callback:
                stream_callback("reasoning", "candidate fails if n=6 because ")
                stream_callback("content", "possible counterexample: ")
            raise RuntimeError("404 model endpoint missing")
        assert "SOFT RESUME CONTEXT" in messages[0]["content"]
        assert "candidate fails if n=6" in messages[0]["content"]
        output = "KEEP after checking the partial counterexample"
        if stream_callback:
            stream_callback("content", output)
        return output, LLMResponse(
            content=output,
            model=self.model,
            prompt_tokens=30,
            completion_tokens=8,
            latency_s=0.01,
            cost_usd=0.002,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )


def make_lab(tmp_path, state, name, retries=2):
    return TheoremResearchLab(
        Trace(name, out_dir=tmp_path / "runs"),
        state,
        literature=EmptyLiterature(),
        toolbox=FakeToolbox(),
        max_retries=retries,
    )


def test_connection_retry_reuses_partial_stream(tmp_path):
    state = ResearchState(tmp_path / "state")
    lab = make_lab(tmp_path, state, "retry", retries=2)
    agent = InterruptThenFinishAgent()

    result = lab._call(agent, "solve this", "iter:1:proposer")
    lab.trace.close()

    assert result == "complete final answer"
    assert agent.calls == 2
    assert lab._partial_get("iter:1:proposer") is None
    cache = lab._cache_get("iter:1:proposer")
    assert cache is not None
    assert cache["status"] == "COMPLETE"
    assert cache["soft_resumed"] is True


def test_new_lab_instance_soft_resumes_nonretryable_interruption(tmp_path):
    state = ResearchState(tmp_path / "state")
    first_lab = make_lab(tmp_path, state, "first", retries=1)
    broken = PartialThen404Agent(fail=True)

    try:
        first_lab._call(broken, "refute candidate", "iter:1:critic")
    except Exception:
        pass
    finally:
        first_lab.trace.close()

    partial = first_lab._partial_get("iter:1:critic")
    assert partial is not None
    assert "candidate fails if n=6" in partial["reasoning"]

    second_lab = make_lab(tmp_path, state, "second", retries=1)
    fixed = PartialThen404Agent(fail=False)
    result = second_lab._call(fixed, "refute candidate", "iter:1:critic")
    second_lab.trace.close()

    assert result.startswith("KEEP")
    assert fixed.calls == 1
    assert "possible counterexample" in fixed.seen_messages[0][0]["content"]
    assert second_lab._partial_get("iter:1:critic") is None
