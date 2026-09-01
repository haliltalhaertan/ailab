from pathlib import Path

from lab.client import LLMResponse
from lab.research_state import ResearchState
from lab.theorem_lab import TheoremResearchLab
from lab.tools import ResearchToolbox
from lab.trace import Trace


class FakeAgent:
    def __init__(self, name: str, outputs: list[str]):
        self.name = name
        self.model = "fake/model"
        self.system_prompt = f"system:{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.outputs = list(outputs)

    def respond(self, messages, stream_callback=None):
        content = self.outputs.pop(0)
        if stream_callback:
            stream_callback("content", content)
        return content, LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            latency_s=0.0,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        return []


def test_theorem_lab_persists_candidate_and_audit(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    trace = Trace("test", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = lab.run(
        "P",
        manager=FakeAgent("manager", ['{"decision":"KEEP","status":"OPEN","reason":"ok","next_task":"next"}']),
        proposer=FakeAgent("proposer", ['{"title":"C","claim":"claim","strategy":"s","evidence_needed":[],"tool_request":{"tool":"none"}}']),
        critic=FakeAgent("critic", ['{"verdict":"KEEP","reason":"no counterexample yet","counterexample":""}']),
        verifier=FakeAgent("verifier", ['{"verdict":"INCONCLUSIVE","reason":"needs proof","formal_proof_required":true,"counterexample":""}']),
        auditor=FakeAgent("auditor", ["PASS-WITH-GAPS"]),
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    candidates = state.list_items(kind="conjecture")
    assert len(candidates) == 1
    assert candidates[0].status == "OPEN"
    assert state.list_items(kind="audit")
    assert "Teorem Araştırması Sonucu" in report

    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"type": "agent_start"' in trace_text
    assert '"type": "state_change"' in trace_text
    assert '"type": "checkpoint"' in trace_text


def test_tropical_counterexample_automatically_kills_candidate(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    trace = Trace("test", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(
        trace,
        state,
        literature=EmptyLiterature(),
        toolbox=ResearchToolbox(script_root=tmp_path / "scripts", lean_root=tmp_path / "formal"),
    )
    bad_request = (
        '{"title":"bad","claim":"direct edge is always optimal","strategy":"test",'
        '"evidence_needed":[],"tool_request":{"tool":"tropical_grid","weights":[0,1,2],'
        '"circuit":{"n":3,"gates":[{"id":"e13","op":"edge","u":1,"v":3}],"output":"e13"}}}'
    )

    lab.run(
        "P",
        manager=FakeAgent("manager", ['{"decision":"KEEP","status":"OPEN","reason":"try","next_task":"next"}']),
        proposer=FakeAgent("proposer", [bad_request]),
        critic=FakeAgent("critic", ['{"verdict":"KEEP","reason":"tool decides","counterexample":""}']),
        verifier=FakeAgent("verifier", ['{"verdict":"INCONCLUSIVE","reason":"tool decides","formal_proof_required":true,"counterexample":""}']),
        auditor=FakeAgent("auditor", ["PASS-WITH-GAPS"]),
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    candidate = state.list_items(kind="conjecture")[0]
    assert candidate.status == "FAIL"
    counterexamples = state.list_items(kind="counterexample")
    assert len(counterexamples) == 1
    assert counterexamples[0].metadata["payload"]["status"] == "COUNTEREXAMPLE"
