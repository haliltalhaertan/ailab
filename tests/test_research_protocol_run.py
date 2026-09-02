from __future__ import annotations

import json
from pathlib import Path

from lab import ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.research_contract import ResearchContract
from lab.tools import ResearchToolbox


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
        self.calls = 0
        self.prompts: list[str] = []

    def respond(self, messages, stream_callback=None):
        self.calls += 1
        self.prompts.append(str(messages[-1].get("content") or ""))
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


def _contract(*, policy: str, target_type: str = "COMPUTE") -> ResearchContract:
    return ResearchContract.from_dict(
        {
            "problem": "P",
            "object_model": "Integers n in the frozen finite range.",
            "validity_definition": "Use exact integer semantics.",
            "equivalence_definition": "Literal equality under the frozen model.",
            "objective": {"type": "compute", "measure": "value"},
            "pilot_policy": policy,
            "open_targets": [
                {
                    "id": "T1",
                    "statement": "Compute values for n=1..2",
                    "target_type": target_type,
                    "status": "OPEN",
                    "scope": {"n": {"type": "integer_range", "min": 1, "max": 2}},
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


def _write_exact_script(root: Path, name: str = "exact.py") -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(
        "\n".join(
            [
                "import json",
                'AILAB_ALLOWED_EVIDENCE_KINDS = {"EXACT_PASS"}',
                'AILAB_EVIDENCE_ROLE = "INDEPENDENT_CHECKER"',
                "print(json.dumps({",
                '    "kind": "EXACT_PASS",',
                '    "termination_reason": "completed",',
                '    "exhaustive": False,',
                '    "covered": {"n": {"type": "integer_range", "min": 1, "max": 2}},',
                "}))",
            ]
        ),
        encoding="utf-8",
    )
    return name


def _agents(*, tool_name: str | None = None, close_target: bool = False, invalid_target: bool = False):
    tool_request = {"tool": "none"}
    if tool_name:
        tool_request = {"tool": "script", "name": tool_name, "args": []}
    target_id = "BAD" if invalid_target else "T1"
    proposal = json.dumps(
        {
            "title": "Target candidate",
            "claim": "Compute values for n=1..2",
            "target_id": target_id,
            "strategy": "deterministic",
            "evidence_needed": [],
            "tool_request": tool_request,
        }
    )
    target_proposal = (
        {"target_id": "T1", "status": "CLOSED", "superseded_by": ""}
        if close_target
        else {"target_id": "", "status": "", "superseded_by": ""}
    )
    manager_status = "COMPUTATION_PASS" if tool_name else "OPEN"
    return {
        "manager": FakeAgent(
            "ResearchManager",
            [
                json.dumps(
                    {
                        "decision": "CHECKPOINT" if close_target else "KEEP",
                        "status": manager_status,
                        "reason": "machine gate decides",
                        "next_task": "next",
                        "target_proposal": target_proposal,
                    }
                )
            ],
        ),
        "proposer": FakeAgent("Theorist", [proposal, proposal] if invalid_target else [proposal]),
        "critic": FakeAgent(
            "AdversarialCritic",
            ['{"verdict":"KEEP","reason":"no refutation","counterexample":""}'],
        ),
        "verifier": FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"PASS","reason":"machine result observed","formal_proof_required":false,"counterexample":""}'],
        ),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }


def _run(lab: TheoremResearchLab, agents: dict[str, FakeAgent]) -> str:
    return lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=0,
    )


def test_required_pilot_gate_stops_before_theorist(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(policy="REQUIRED")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="P")
    agents = _agents()
    trace = Trace("pilot-negative", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = _run(lab, agents)
    trace.close()

    assert "hata nedeniyle" in report
    assert agents["proposer"].calls == 0
    runtime = json.loads((state.root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["research_phase"] == "PILOT"
    assert runtime["completed_iterations"] == 0


def test_bound_pilot_opens_run_and_is_injected_into_theorist_prompt(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(policy="REQUIRED")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="P")
    script_root = tmp_path / "research_tools"
    script_name = _write_exact_script(script_root)
    toolbox = ResearchToolbox(
        script_root=script_root,
        lean_root=state.root / "formal",
        problem_pack_root=None,
    )
    trace = Trace("pilot-positive", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)

    pilot = lab.run_pilot(target_id="T1", script_name=script_name)
    pilot_evidence = dict(pilot.metadata["evidence"])
    assert pilot_evidence["kind"] == "EXACT_PASS"
    assert pilot_evidence["contract_hash"] == contract.contract_hash
    assert pilot_evidence["target_id"] == "T1"

    agents = _agents()
    report = _run(lab, agents)
    trace.close()

    assert "hata nedeniyle" not in report
    assert agents["proposer"].calls == 1
    assert "DETERMINISTIC PILOT EVIDENCE" in agents["proposer"].prompts[0]
    assert "tool_sha256=" in agents["proposer"].prompts[0]
    conjecture = state.list_items(kind="conjecture")[-1]
    assert conjecture.metadata["target_id"] == "T1"
    assert conjecture.metadata["claim_role"] == "TARGET_RESOLUTION"
    assert conjecture.metadata.get("pilot_missing") is not True


def test_target_closes_only_after_bound_run_level_machine_evidence(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(policy="NOT_APPLICABLE")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="P")
    script_root = tmp_path / "research_tools"
    script_name = _write_exact_script(script_root)
    toolbox = ResearchToolbox(
        script_root=script_root,
        lean_root=state.root / "formal",
        problem_pack_root=None,
    )
    agents = _agents(tool_name=script_name, close_target=True)
    trace = Trace("target-positive", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)

    report = _run(lab, agents)
    trace.close()

    assert "hata nedeniyle" not in report
    loaded = ResearchContract.load(state.root)
    target = loaded.target("T1")
    assert target.status == "CLOSED"
    assert target.closed_by
    conjecture = state.list_items(kind="conjecture")[-1]
    assert conjecture.status == "COMPUTATION_PASS"
    assert conjecture.metadata["claim_role"] == "TARGET_RESOLUTION"
    assert conjecture.metadata["evidence"]["resolution_scope"] == "TARGET_RESOLUTION"


def test_manager_cannot_close_target_without_machine_evidence(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(policy="NOT_APPLICABLE")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="P")
    agents = _agents(close_target=True)
    trace = Trace("target-negative", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = _run(lab, agents)
    trace.close()

    assert "hata nedeniyle" not in report
    assert ResearchContract.load(state.root).target("T1").status == "OPEN"
    conjecture = state.list_items(kind="conjecture")[-1]
    assert conjecture.metadata["claim_role"] == "TARGET_RESOLUTION"
    assert "evidence" not in conjecture.metadata


def test_invalid_target_fails_closed_after_one_repair_attempt(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(policy="NOT_APPLICABLE")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="P")
    agents = _agents(invalid_target=True)
    trace = Trace("target-selection-negative", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = _run(lab, agents)
    trace.close()

    assert "hata nedeniyle" in report
    assert agents["proposer"].calls == 2
    assert state.list_items(kind="conjecture") == []
