from dataclasses import dataclass
from pathlib import Path

from lab.research_state import ResearchState
from lab.theorem_lab import TheoremResearchLab
from lab.tools import ResearchToolbox
from lab.trace import Trace


@dataclass
class FakeResponse:
    content: str
    model: str = "fake/model"
    prompt_tokens: int = 1
    completion_tokens: int = 1
    latency_s: float = 0.0


class FakeAgent:
    def __init__(self, name: str, outputs: list[str]):
        self.name = name
        self.temperature = 0.0
        self.outputs = list(outputs)

    def respond(self, messages):
        content = self.outputs.pop(0)
        return content, FakeResponse(content)


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
        critic=FakeAgent("critic", ["KEEP: no counterexample yet"]),
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
    assert "Theorem Research Run" in report


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
        critic=FakeAgent("critic", ["KEEP"]),
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
