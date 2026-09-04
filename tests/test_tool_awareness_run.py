from pathlib import Path

from lab.client import LLMResponse
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.tools import ResearchToolbox
from lab.trace import Trace
from lab.worker import _bind_special_tool_guards


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


def _row(available: bool, reason: str) -> dict[str, object]:
    return {"available": available, "reason": reason}


def test_real_theorem_run_fails_closed_when_model_requests_closed_lean(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    trace = Trace("tool-awareness-negative", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(
        trace,
        state,
        literature=EmptyLiterature(),
        toolbox=ResearchToolbox(
            script_root=tmp_path / "scripts",
            problem_pack_root=None,
            lean_root=tmp_path / "formal",
        ),
    )
    effective = {
        "lean_draft": _row(False, "frozen Lean off"),
        "z3": _row(False, "closed for test"),
        "script": _row(False, "closed for test"),
        "tropical_grid": _row(True, "built-in"),
        "code_experiment": _row(False, "closed for test"),
    }
    lab.registry.set_effective_availability(effective)
    _bind_special_tool_guards(lab, {"effective_tool_availability": effective})

    proposal = (
        '{"title":"closed lean request","claim":"True is provable","strategy":"formal",'
        '"evidence_needed":["formal proof"],"tool_request":{"tool":"lean_draft",'
        '"theorem_name":"closedLean","theorem_type":"True",'
        '"source":"theorem closedLean : True := by trivial"}}'
    )
    report = lab.run(
        "P",
        manager=FakeAgent(
            "manager",
            ['{"decision":"KEEP","status":"OPEN","reason":"tool unavailable","next_task":"use an available checker"}'],
        ),
        proposer=FakeAgent("proposer", [proposal]),
        critic=FakeAgent(
            "critic",
            ['{"verdict":"KEEP","reason":"no deterministic refutation","counterexample":""}'],
        ),
        verifier=FakeAgent(
            "verifier",
            ['{"verdict":"INCONCLUSIVE","reason":"Lean unavailable","formal_proof_required":true,"counterexample":""}'],
        ),
        auditor=FakeAgent("auditor", ["PASS-WITH-GAPS"]),
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    candidate = state.list_items(kind="conjecture")[0]
    assert candidate.status == "OPEN"
    assert "Teorem Araştırması Sonucu" in report
    candidates_dir = state.root / "formal" / "candidates"
    assert not list(candidates_dir.glob("*.lean"))
    trace_text = trace.path.read_text(encoding="utf-8")
    assert "frozen Lean off" in trace_text
    assert '"tool_unavailable": true' in trace_text


def test_real_theorem_run_uses_open_tropical_checker_and_promotes_computation(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    trace = Trace("tool-awareness-positive", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(
        trace,
        state,
        literature=EmptyLiterature(),
        toolbox=ResearchToolbox(
            script_root=tmp_path / "scripts",
            problem_pack_root=None,
            lean_root=tmp_path / "formal",
        ),
    )
    effective = {
        "lean_draft": _row(False, "closed for test"),
        "z3": _row(False, "closed for test"),
        "script": _row(False, "closed for test"),
        "tropical_grid": _row(True, "built-in deterministic checker"),
        "code_experiment": _row(False, "closed for test"),
    }
    lab.registry.set_effective_availability(effective)
    _bind_special_tool_guards(lab, {"effective_tool_availability": effective})

    proposal = (
        '{"title":"n2 exact grid candidate",'
        '"claim":"For n=2 the one-edge tropical circuit agrees with the shortest-path function on weights 0 and 1",'
        '"strategy":"check the complete two-value grid","evidence_needed":["deterministic grid check"],'
        '"tool_request":{"tool":"tropical_grid","weights":[0,1],'
        '"circuit":{"n":2,"gates":[{"id":"e12","op":"edge","u":1,"v":2}],"output":"e12"}}}'
    )
    lab.run(
        "P",
        manager=FakeAgent(
            "manager",
            ['{"decision":"KEEP","status":"COMPUTATION_PASS","reason":"deterministic grid evidence","next_task":"generalize"}'],
        ),
        proposer=FakeAgent("proposer", [proposal]),
        critic=FakeAgent(
            "critic",
            ['{"verdict":"KEEP","reason":"finite claim matches tested scope","counterexample":""}'],
        ),
        verifier=FakeAgent(
            "verifier",
            ['{"verdict":"PASS","reason":"deterministic checker verifies the stated finite-grid claim","formal_proof_required":false,"counterexample":""}'],
        ),
        auditor=FakeAgent("auditor", ["PASS"]),
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    candidate = state.list_items(kind="conjecture")[0]
    assert candidate.status == "COMPUTATION_PASS"
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"tool": "tropical_grid"' in trace_text
    assert '"status": "GRID_PASS"' in trace_text
    assert '"cases_checked": 2' in trace_text
