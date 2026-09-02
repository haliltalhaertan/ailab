from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab import ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.research_contract import ResearchContract, scope_covers


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


def _contract(problem: str = "P") -> ResearchContract:
    return ResearchContract.from_dict(
        {
            "contract_version": 1,
            "problem": problem,
            "object_model": "Finite integers.",
            "validity_definition": "Candidate statement is evaluated under the frozen domain.",
            "equivalence_definition": "Literal mathematical equality under the frozen model.",
            "objective": {"type": "compute", "measure": "value"},
            "pilot_policy": "OPTIONAL",
            "known_results": [],
            "open_targets": [
                {
                    "id": "T1",
                    "statement": "Compute values for n=3..10",
                    "target_type": "COMPUTE",
                    "status": "OPEN",
                    "scope": {
                        "n": {"type": "integer_range", "min": 3, "max": 10}
                    },
                }
            ],
            "forbidden_claims": ["unscoped asymptotic claims"],
            "evidence_policy": {
                "numerical": "OPEN",
                "deterministic_computation": "COMPUTATION_PASS",
                "exhaustive_computation": "COMPUTATION_PASS",
                "formal_proof": "PROVEN",
            },
            "parameters": {"semantics": "exact"},
        }
    )


def _agents():
    return {
        "manager": FakeAgent(
            "ResearchManager",
            ['{"decision":"KEEP","status":"OPEN","reason":"continue","next_task":"next"}'],
        ),
        "proposer": FakeAgent(
            "Theorist",
            [
                json.dumps(
                    {
                        "title": "Small claim",
                        "claim": "3 + 4 = 7",
                        "target_id": "T1",
                        "strategy": "direct",
                        "evidence_needed": [],
                        "tool_request": {"tool": "none"},
                    }
                )
            ],
        ),
        "critic": FakeAgent(
            "AdversarialCritic",
            ['{"verdict":"REVISE","reason":"no machine proof","counterexample":""}'],
        ),
        "verifier": FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"INCONCLUSIVE","reason":"no tool","formal_proof_required":false,"counterexample":""}'],
        ),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }


def test_contract_round_trip_freeze_hashes_semantic_policy(tmp_path: Path):
    contract = _contract()
    contract.save(tmp_path)
    digest = contract.freeze(tmp_path, frozen_problem="P")
    loaded = ResearchContract.load(tmp_path)

    assert loaded.frozen is True
    assert loaded.contract_hash == digest
    assert loaded.target("T1").target_hash
    assert loaded.resolution_scope(
        "T1",
        {"n": {"type": "integer_range", "min": 1, "max": 12}},
    ) == "TARGET_RESOLUTION"
    assert loaded.resolution_scope(
        "T1",
        {"n": {"type": "integer_range", "min": 3, "max": 5}},
    ) == "PARTIAL"

    raw = loaded.to_dict()
    raw["evidence_policy"]["numerical"] = "COMPUTATION_PASS"
    with pytest.raises(ValueError, match="hash mismatch"):
        ResearchContract.from_dict(raw)


def test_scope_types_are_unambiguous_and_fail_closed():
    assert scope_covers(
        {"n": {"type": "integer_range", "min": 3, "max": 10}},
        {"n": {"type": "integer_range", "min": 3, "max": 10}},
    )
    assert not scope_covers(
        {"n": {"type": "enum", "values": [3, 10]}},
        {"n": {"type": "integer_range", "min": 3, "max": 10}},
    )
    with pytest.raises(ValueError, match="unsupported scope type"):
        _contract()._target_from_dict(
            {
                "id": "bad",
                "statement": "bad",
                "target_type": "COMPUTE",
                "scope": {"n": {"type": "float_range", "min": 0, "max": 1}},
            }
        )


def test_run_level_contract_binding_positive_and_checkpoint_hash(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract("P")
    contract.save(state.root)
    digest = contract.freeze(state.root, frozen_problem="P")
    agents = _agents()
    trace = Trace("contract-positive", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = lab.run(
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

    assert "hata nedeniyle" not in report
    frozen = json.loads(state.problem_path.read_text(encoding="utf-8"))
    assert frozen["metadata"]["contract_hash"] == digest
    checkpoint = json.loads(sorted(state.checkpoint_dir.glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert checkpoint["contract_hash"] == digest
    assert agents["proposer"].calls == 1


def test_run_level_contract_problem_mismatch_stops_before_theorist(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    contract = _contract("Different problem")
    contract.save(state.root)
    contract.freeze(state.root, frozen_problem="Different problem")
    agents = _agents()
    trace = Trace("contract-negative", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    report = lab.run(
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

    assert "hata nedeniyle" in report
    assert agents["proposer"].calls == 0
    assert "contract" in json.loads(state.root.joinpath("runtime.json").read_text(encoding="utf-8"))["last_error"].lower()
