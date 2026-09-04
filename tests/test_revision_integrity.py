from __future__ import annotations

import hashlib
from pathlib import Path

from lab.integrity import sha256_file
from lab.research_state import ResearchState


def _formal_metadata(state: ResearchState, item_id: str, claim: str) -> dict:
    candidate_dir = state.root / "formal" / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_dir / "revision-one.lean"
    candidate.write_text("theorem bound : 1 = 1 := by rfl\n", encoding="utf-8")
    return {
        "item_id": item_id,
        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
        "lean_file": candidate.name,
        "lean_sha256": sha256_file(candidate),
        "theorem_name": "bound",
        "theorem_type": "1 = 1",
        "iteration": 1,
        "axioms": [],
        "formal_verified": True,
        "formal_binding_verified": True,
        "axioms_verified": True,
        "source_clean": True,
    }


def test_proven_revision_stays_historical_after_claim_revision(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    item = state.add_item(
        "conjecture",
        "Revision one",
        "First claim",
        metadata={"iteration": 1},
    )
    state.update_item(
        item.id,
        status="PROVEN",
        metadata=_formal_metadata(state, item.id, item.claim),
    )

    proven = state.get(item.id)
    assert proven.status == "PROVEN"
    assert proven.revisions[0]["status"] == "PROVEN"

    revised = state.revise_item(
        item.id,
        title="Revision two",
        claim="Second claim",
        iteration=2,
        metadata={
            "proposal": {
                "title": "Revision two",
                "claim": "Second claim",
                "revises": item.id,
                "strategy": "change the claim",
                "evidence_needed": ["fresh proof"],
                "tool_request": {"tool": "none"},
            }
        },
    )

    assert revised.id == item.id
    assert revised.current_revision == 2
    assert revised.status == "OPEN"
    assert revised.revisions[0]["status"] == "PROVEN"
    assert revised.revisions[1]["status"] == "OPEN"
    assert revised.revisions[0]["claim"] == "First claim"
    assert revised.revisions[1]["claim"] == "Second claim"
    assert revised.revisions[0]["claim_hash"] != revised.revisions[1]["claim_hash"]
    assert "proof_seal" not in revised.metadata
    assert "formal_verified" not in revised.metadata


def test_proposal_tool_source_is_persisted_as_hashed_artifact(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    smt2 = "(set-logic QF_LIA)\n(assert (= 1 1))\n(check-sat)\n"
    item = state.add_item(
        "conjecture",
        "Z3 candidate",
        "The solver query is satisfiable",
        metadata={
            "iteration": 1,
            "proposal": {
                "title": "Z3 candidate",
                "claim": "The solver query is satisfiable",
                "strategy": "ask an exact solver",
                "evidence_needed": ["solver result"],
                "tool_request": {"tool": "z3", "smt2": smt2},
            },
        },
    )

    summary = item.metadata["tool_request_summary"]
    artifact = state.root / summary["artifact_path"]
    assert summary["artifact_path"] == f"formal/{item.id}/r1.smt2"
    assert artifact.read_text(encoding="utf-8") == smt2
    assert summary["sha256"] == sha256_file(artifact)
    assert "smt2" not in item.metadata["proposal"]["tool_request"]
