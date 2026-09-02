import hashlib
from pathlib import Path

from lab.client import LLMResponse
from lab.integrity import sha256_file
from lab.integrity_theorem_lab import TheoremResearchLab
from lab.research_state import ResearchState
from lab.tools import ResearchToolbox, ToolResult
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


class BoundFormalEvidenceLab(TheoremResearchLab):
    """Inject machine-shaped formal evidence after proposal/item binding.

    This test double deliberately bypasses Lean execution. The LeanTool itself has
    separate checker tests; here we are testing that run() carries already-verified,
    claim-bound evidence all the way through StatusGuard and ResearchState.
    """

    def __init__(self, *args, valid_axioms: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.valid_axioms = valid_axioms

    def _tool(self, request, step_key):
        if str((request or {}).get("tool") or "").lower() != "lean_draft":
            return super()._tool(request, step_key)
        assert self._active_iteration is not None
        assert self._active_item_id
        candidate_dir = self.state.root / "formal" / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        filename = f"iter-{self._active_iteration}-{self._active_item_id}.lean"
        candidate = candidate_dir / filename
        candidate.write_text("theorem bound : 1 = 1 := by rfl\n", encoding="utf-8")
        item = self.state.get(self._active_item_id)
        metadata = {
            "formal_verified": True,
            "formal_binding_verified": True,
            "axioms_verified": self.valid_axioms,
            "source_clean": True,
            "claim_hash": self._active_claim_hash,
            "claim_sha256": hashlib.sha256(item.claim.encode("utf-8")).hexdigest(),
            "item_id": self._active_item_id,
            "iteration": self._active_iteration,
            "file": filename,
            "lean_file": filename,
            "lean_sha256": sha256_file(candidate),
            "theorem_name": "bound",
            "theorem_type": "1 = 1",
            "axioms": [],
        }
        return ToolResult(True, "lean", output="verified", metadata=metadata)


def _formal_proposal() -> str:
    return (
        '{"title":"Bound","claim":"1 = 1","strategy":"formal",'
        '"evidence_needed":["Lean"],"tool_request":{"tool":"lean_draft",'
        '"theorem_name":"bound","theorem_type":"1 = 1",'
        '"source":"theorem bound : 1 = 1 := by rfl"}}'
    )


def _run_formal_case(tmp_path: Path, *, valid_axioms: bool):
    state = ResearchState(tmp_path / "state")
    trace = Trace("formal-run-gate", out_dir=tmp_path / "runs")
    lab = BoundFormalEvidenceLab(
        trace,
        state,
        literature=EmptyLiterature(),
        valid_axioms=valid_axioms,
    )
    report = lab.run(
        "P",
        manager=FakeAgent(
            "manager",
            ['{"decision":"KEEP","status":"PROVEN","reason":"formal evidence","next_task":"next"}'],
        ),
        proposer=FakeAgent("proposer", [_formal_proposal()]),
        critic=FakeAgent("critic", ['{"verdict":"KEEP","reason":"no objection","counterexample":""}']),
        verifier=FakeAgent(
            "verifier",
            ['{"verdict":"PASS","reason":"bound formal evidence","formal_proof_required":false,"counterexample":""}'],
        ),
        auditor=FakeAgent("auditor", ["PASS"]),
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()
    return state, trace, report


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
    final_checkpoints = list((state.root / "checkpoints").glob("*final*"))
    assert final_checkpoints
    assert "Teorem Araştırması Sonucu" in report

    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"type": "agent_start"' in trace_text
    assert '"type": "state_change"' in trace_text
    assert '"type": "checkpoint"' in trace_text
    assert '"final": true' in trace_text
    assert "PASS-WITH-GAPS" in trace_text


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


def test_run_promotes_only_fully_bound_formal_evidence_to_proven(tmp_path: Path):
    state, trace, report = _run_formal_case(tmp_path, valid_axioms=True)
    candidate = state.list_items(kind="conjecture")[0]
    assert candidate.status == "PROVEN"
    assert candidate.metadata["formal_verified"] is True
    assert candidate.metadata["formal_binding_verified"] is True
    assert candidate.metadata["axioms_verified"] is True
    assert candidate.metadata["source_clean"] is True
    assert candidate.metadata["proof_seal"]
    assert "Teorem Araştırması Sonucu" in report
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"new_status": "PROVEN"' in trace_text


def test_run_downgrades_proven_when_formal_evidence_is_incomplete(tmp_path: Path):
    state, trace, _ = _run_formal_case(tmp_path, valid_axioms=False)
    candidate = state.list_items(kind="conjecture")[0]
    assert candidate.status == "OPEN"
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"type": "status_downgraded_by_guard"' in trace_text
    assert '"requested": "PROVEN"' in trace_text
    assert '"granted": "OPEN"' in trace_text
