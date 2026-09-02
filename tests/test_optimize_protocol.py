from __future__ import annotations

import json
from pathlib import Path

from lab import ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.evidence import candidate_sha256, evidence_from_tool_result
from lab.research_contract import ResearchContract
from lab.tools import ResearchToolbox, ScriptTool


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        del query, limit
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

    def respond(self, messages, stream_callback=None):
        self.calls += 1
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


def _candidate() -> dict:
    return {
        "size": 2,
        "structure": [{"op": "min", "args": ["x", "y"]}],
        "meta": {"b": 2, "a": 1},
    }


def _write_candidate_script(
    root: Path,
    *,
    name: str,
    kind: str,
    role: str,
    covered_max: int = 2,
    declared_sha: str | None = None,
    candidate: dict | None = None,
) -> str:
    root.mkdir(parents=True, exist_ok=True)
    value = candidate if candidate is not None else _candidate()
    digest = declared_sha if declared_sha is not None else candidate_sha256(value)
    payload = {
        "kind": kind,
        "termination_reason": "completed",
        "exhaustive": kind.startswith("EXHAUSTIVE_"),
        "covered": {"n": {"type": "integer_range", "min": 1, "max": covered_max}},
        "candidate": value,
        "candidate_sha256": digest,
    }
    (root / name).write_text(
        "\n".join(
            [
                "import json",
                f"AILAB_ALLOWED_EVIDENCE_KINDS = {repr({kind})}",
                f"AILAB_EVIDENCE_ROLE = {role!r}",
                f"print(json.dumps({payload!r}, sort_keys=True))",
            ]
        ),
        encoding="utf-8",
    )
    return name


def _contract(root: Path, *, target_type: str) -> ResearchContract:
    statement = "Find the exact optimum candidate" if target_type == "OPTIMIZE" else "Compute values for n=1..2"
    contract = ResearchContract.from_dict(
        {
            "problem": "P",
            "object_model": "Finite exact objects over n=1..2.",
            "validity_definition": "Checked-in deterministic semantics decide validity.",
            "equivalence_definition": "Canonical JSON candidate identity and exact checked result.",
            "objective": {"type": "minimize" if target_type == "OPTIMIZE" else "compute", "measure": "size"},
            "pilot_policy": "NOT_APPLICABLE",
            "open_targets": [
                {
                    "id": "T1",
                    "statement": statement,
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
    contract.save(root)
    contract.freeze(root, frozen_problem="P")
    return contract


def _proposal(statement: str, script_name: str) -> str:
    return json.dumps(
        {
            "title": "Target-resolution candidate",
            "claim": statement,
            "target_id": "T1",
            "strategy": "checked-in deterministic tool",
            "evidence_needed": ["bound machine evidence"],
            "tool_request": {"tool": "script", "name": script_name, "args": []},
        }
    )


def _manager(*, close: bool) -> str:
    return json.dumps(
        {
            "decision": "CHECKPOINT" if close else "KEEP",
            "status": "COMPUTATION_PASS",
            "reason": "machine evidence decides",
            "next_task": "verify independently",
            "target_proposal": (
                {"target_id": "T1", "status": "CLOSED", "superseded_by": ""}
                if close
                else {"target_id": "", "status": "", "superseded_by": ""}
            ),
        }
    )


def _run_optimize(tmp_path: Path, *, checker_covered_max: int) -> tuple[ResearchState, dict[str, FakeAgent], str]:
    state = ResearchState(tmp_path / "state")
    contract = _contract(state.root, target_type="OPTIMIZE")
    statement = contract.target("T1").statement
    scripts = tmp_path / "research_tools"
    search_name = _write_candidate_script(
        scripts,
        name="search_optimum.py",
        kind="EXHAUSTIVE_OPTIMUM",
        role="SEARCH_CERTIFICATE",
    )
    checker_name = _write_candidate_script(
        scripts,
        name="check_optimum.py",
        kind="EXACT_PASS",
        role="INDEPENDENT_CHECKER",
        covered_max=checker_covered_max,
    )
    toolbox = ResearchToolbox(script_root=scripts, lean_root=state.root / "formal", problem_pack_root=None)
    agents = {
        "proposer": FakeAgent("Theorist", [_proposal(statement, search_name), _proposal(statement, checker_name)]),
        "verifier": FakeAgent(
            "VerificationEngineer",
            [
                '{"verdict":"PASS","reason":"search certificate observed","formal_proof_required":false,"counterexample":""}',
                '{"verdict":"PASS","reason":"independent checker observed","formal_proof_required":false,"counterexample":""}',
            ],
        ),
        "critic": FakeAgent(
            "AdversarialCritic",
            [
                '{"verdict":"KEEP","reason":"search remains to be checked","counterexample":""}',
                '{"verdict":"KEEP","reason":"independent agreement","counterexample":""}',
            ],
        ),
        "manager": FakeAgent("ResearchManager", [_manager(close=False), _manager(close=True)]),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }
    trace = Trace("optimize-run", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)
    report = lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=2,
        checkpoint_every=0,
    )
    trace.close()
    return state, agents, report


def test_script_adapter_recomputes_canonical_candidate_hash(tmp_path: Path):
    scripts = tmp_path / "research_tools"
    value = _candidate()
    reordered = {"meta": {"a": 1, "b": 2}, "structure": value["structure"], "size": 2}
    assert candidate_sha256(value) == candidate_sha256(reordered)
    name = _write_candidate_script(
        scripts,
        name="search.py",
        kind="EXHAUSTIVE_OPTIMUM",
        role="SEARCH_CERTIFICATE",
        candidate=reordered,
        declared_sha=candidate_sha256(value),
    )

    result = ScriptTool(scripts).run(name)
    evidence = evidence_from_tool_result(result, request={"tool": "script", "name": name, "args": []})

    assert evidence.kind == "EXHAUSTIVE_OPTIMUM"
    assert evidence.metadata["candidate_sha256"] == candidate_sha256(value)


def test_script_adapter_downgrades_mismatched_declared_candidate_hash(tmp_path: Path):
    scripts = tmp_path / "research_tools"
    name = _write_candidate_script(
        scripts,
        name="search.py",
        kind="EXHAUSTIVE_OPTIMUM",
        role="SEARCH_CERTIFICATE",
        declared_sha="0" * 64,
    )

    result = ScriptTool(scripts).run(name)
    evidence = evidence_from_tool_result(result, request={"tool": "script", "name": name, "args": []})

    assert evidence.kind == "INCONCLUSIVE"
    assert evidence.ok is False
    assert evidence.metadata["downgraded_from"] == "EXHAUSTIVE_OPTIMUM"
    assert "candidate_sha256 mismatch" in evidence.metadata["evidence_downgrade_reason"]


def test_run_level_optimize_closes_only_when_independent_tools_bind_same_full_scope_candidate(tmp_path: Path):
    state, agents, report = _run_optimize(tmp_path, checker_covered_max=2)

    assert "hata nedeniyle" not in report
    target = ResearchContract.load(state.root).target("T1")
    assert target.status == "CLOSED"
    assert len(target.closed_by) == 2
    assert target.metadata["candidate_sha256"] == candidate_sha256(_candidate())
    assert agents["proposer"].calls == 2
    items = state.list_items(kind="conjecture")
    assert items[0].metadata["evidence"]["evidence_role"] == "SEARCH_CERTIFICATE"
    assert items[1].metadata["evidence"]["evidence_role"] == "INDEPENDENT_CHECKER"
    assert items[0].metadata["evidence"]["tool_sha256"] != items[1].metadata["evidence"]["tool_sha256"]


def test_run_level_optimize_rejects_partial_scope_checker(tmp_path: Path):
    state, _agents, report = _run_optimize(tmp_path, checker_covered_max=1)

    assert "hata nedeniyle" not in report
    target = ResearchContract.load(state.root).target("T1")
    assert target.status == "OPEN"
    items = state.list_items(kind="conjecture")
    assert items[0].metadata["evidence"]["resolution_scope"] == "TARGET_RESOLUTION"
    assert items[1].metadata["evidence"]["resolution_scope"] == "PARTIAL"


def test_run_finishes_cleanly_when_only_target_closes_before_iteration_budget(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract(state.root, target_type="COMPUTE")
    statement = contract.target("T1").statement
    scripts = tmp_path / "research_tools"
    exact_name = _write_candidate_script(
        scripts,
        name="exact.py",
        kind="EXACT_PASS",
        role="INDEPENDENT_CHECKER",
    )
    toolbox = ResearchToolbox(script_root=scripts, lean_root=state.root / "formal", problem_pack_root=None)
    agents = {
        "proposer": FakeAgent("Theorist", [_proposal(statement, exact_name)]),
        "verifier": FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"PASS","reason":"exact result","formal_proof_required":false,"counterexample":""}'],
        ),
        "critic": FakeAgent("AdversarialCritic", ['{"verdict":"KEEP","reason":"exact","counterexample":""}']),
        "manager": FakeAgent("ResearchManager", [_manager(close=True)]),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }
    trace = Trace("early-resolution-run", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)

    report = lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=2,
        checkpoint_every=0,
    )
    trace.close()

    assert "hata nedeniyle" not in report
    assert ResearchContract.load(state.root).target("T1").status == "CLOSED"
    assert agents["proposer"].calls == 1
    runtime = json.loads((state.root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["completed_iterations"] == 1


def test_full_scope_compute_pilot_closes_without_llm_manager(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    _contract(state.root, target_type="COMPUTE")
    scripts = tmp_path / "research_tools"
    exact_name = _write_candidate_script(
        scripts,
        name="pilot_exact.py",
        kind="EXACT_PASS",
        role="INDEPENDENT_CHECKER",
    )
    toolbox = ResearchToolbox(script_root=scripts, lean_root=state.root / "formal", problem_pack_root=None)
    trace = Trace("pilot-auto-close", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature(), toolbox=toolbox)

    item = lab.run_pilot(target_id="T1", script_name=exact_name)
    trace.close()

    target = ResearchContract.load(state.root).target("T1")
    assert target.status == "CLOSED"
    assert target.closed_by
    assert item.kind == "experiment"
    assert item.metadata["pilot"] is True
    assert item.metadata["evidence"]["resolution_scope"] == "TARGET_RESOLUTION"
