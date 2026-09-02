from __future__ import annotations

import json
from pathlib import Path

from lab import ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.evidence import evidence_from_tool_result
from lab.research_contract import ResearchContract
from lab.tools import ResearchToolbox, ToolResult


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        return []


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


def _contract(root: Path, *, problem: str = "P") -> ResearchContract:
    contract = ResearchContract.from_dict(
        {
            "problem": problem,
            "object_model": "Finite integer instances.",
            "validity_definition": "Checked-in semantics decide validity.",
            "equivalence_definition": "Exact output equality.",
            "objective": {"type": "compute", "measure": "value"},
            "pilot_policy": "OPTIONAL",
            "open_targets": [
                {
                    "id": "T1",
                    "statement": "Compute n=3",
                    "target_type": "COMPUTE",
                    "scope": {"n": {"type": "integer_range", "min": 3, "max": 3}},
                }
            ],
            "evidence_policy": {
                "numerical": "OPEN",
                "deterministic_computation": "COMPUTATION_PASS",
                "exhaustive_computation": "COMPUTATION_PASS",
                "formal_proof": "PROVEN",
            },
        }
    )
    contract.save(root)
    contract.freeze(root, frozen_problem=problem)
    return contract


def _agents(tool_request: dict, requested_status: str = "COMPUTATION_PASS"):
    proposal = {
        "title": "Evidence candidate",
        "claim": "Compute n=3",
        "target_id": "T1",
        "strategy": "machine check",
        "evidence_needed": ["deterministic tool"],
        "tool_request": tool_request,
    }
    return {
        "manager": FakeAgent(
            "ResearchManager",
            [json.dumps({"decision": "KEEP", "status": requested_status, "reason": "machine result", "next_task": "next"})],
        ),
        "proposer": FakeAgent("Theorist", [json.dumps(proposal)]),
        "critic": FakeAgent("AdversarialCritic", ['{"verdict":"KEEP","reason":"ok","counterexample":""}']),
        "verifier": FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"PASS","reason":"machine output","formal_proof_required":false,"counterexample":""}'],
        ),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }


def _run(tmp_path: Path, tool_request: dict, *, toolbox: ResearchToolbox | None = None):
    state = ResearchState(tmp_path / "state")
    _contract(state.root)
    agents = _agents(tool_request)
    trace = Trace("evidence-run", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)
    lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()
    return state, trace


def test_generic_z3_is_only_solver_result():
    result = ToolResult(
        True,
        "z3",
        output='{"result":"unsat","model":""}',
        metadata={"result": "unsat", "assertion_count": 1},
    )
    evidence = evidence_from_tool_result(result, request={"tool": "z3", "smt2": "(assert false)"})
    assert evidence.kind == "SOLVER_RESULT"
    assert evidence.ok is True


def test_generated_code_experiment_cannot_claim_exact_pass():
    result = ToolResult(
        True,
        "code_experiment",
        output="done",
        metadata={"successful_run_count": 1, "declared_kind": "EXACT_PASS"},
    )
    evidence = evidence_from_tool_result(result)
    assert evidence.source_origin == "GENERATED"
    assert evidence.kind == "NUMERICAL_PASS"


def test_run_level_generic_z3_unsat_cannot_open_computation_pass(tmp_path: Path):
    state, trace = _run(tmp_path, {"tool": "z3", "smt2": "(assert false)"})
    item = state.list_items(kind="conjecture")[0]
    assert item.status == "OPEN"
    text = trace.path.read_text(encoding="utf-8")
    assert '"evidence_kind": "SOLVER_RESULT"' in text
    assert '"type": "status_downgraded_by_guard"' in text


def test_run_level_checked_in_exact_script_can_open_computation_pass(tmp_path: Path):
    scripts = tmp_path / "research_tools"
    scripts.mkdir()
    (scripts / "exact.py").write_text(
        "AILAB_ALLOWED_EVIDENCE_KINDS = ('EXACT_PASS', 'DETERMINISTIC_COUNTEREXAMPLE', 'INCONCLUSIVE')\n"
        "AILAB_ACCEPTS_SPECIFICATION = False\n"
        "AILAB_EVIDENCE_ROLE = 'INDEPENDENT_CHECKER'\n"
        "import json\n"
        "print(json.dumps({'kind':'EXACT_PASS','exhaustive':False,'termination_reason':'completed',"
        "'witness':None,'covered':{'n':{'type':'integer_range','min':3,'max':3}},'result':3}))\n",
        encoding="utf-8",
    )
    toolbox = ResearchToolbox(script_root=scripts, lean_root=tmp_path / "formal")
    state, trace = _run(tmp_path, {"tool": "script", "name": "exact.py", "args": []}, toolbox=toolbox)
    item = state.list_items(kind="conjecture")[0]
    assert item.status == "COMPUTATION_PASS"
    assert item.metadata["evidence"]["kind"] == "EXACT_PASS"
    assert item.metadata["evidence"]["resolution_scope"] == "TARGET_RESOLUTION"
    assert '"evidence_kind": "EXACT_PASS"' in trace.path.read_text(encoding="utf-8")


def test_run_level_script_cannot_self_promote_to_formal_proof(tmp_path: Path):
    scripts = tmp_path / "research_tools"
    scripts.mkdir()
    (scripts / "bad.py").write_text(
        "AILAB_ALLOWED_EVIDENCE_KINDS = ('FORMAL_PROOF',)\n"
        "AILAB_ACCEPTS_SPECIFICATION = False\n"
        "AILAB_EVIDENCE_ROLE = 'GENERAL'\n"
        "import json\n"
        "print(json.dumps({'kind':'FORMAL_PROOF','exhaustive':False,'termination_reason':'completed','witness':None}))\n",
        encoding="utf-8",
    )
    toolbox = ResearchToolbox(script_root=scripts, lean_root=tmp_path / "formal")
    state, trace = _run(tmp_path, {"tool": "script", "name": "bad.py", "args": []}, toolbox=toolbox)
    item = state.list_items(kind="conjecture")[0]
    assert item.status == "OPEN"
    assert '"evidence_kind": "INCONCLUSIVE"' in trace.path.read_text(encoding="utf-8")
