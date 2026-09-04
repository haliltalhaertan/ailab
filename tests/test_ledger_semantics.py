from pathlib import Path

from lab.ledger_semantics import (
    clear_revision_bound_metadata,
    persist_tool_artifact,
    revision_record,
    verification_claim_annotation,
)


def test_self_verification_claim_requires_matching_evidence_kind():
    flagged = verification_claim_annotation(
        "Lean-verified lemma",
        "This candidate is proven.",
        status="OPEN",
        metadata={},
    )
    assert flagged["self_verification_claim"] is True
    assert flagged["verification_claim_supported"] is False
    assert set(flagged["verification_claim_terms"]) == {"formal_verified", "proven"}

    verified = verification_claim_annotation(
        "Verified finite computation",
        "Candidate",
        status="COMPUTATION_PASS",
        metadata={"evidence": {"kind": "EXACT_PASS"}},
    )
    assert verified["verification_claim_supported"] is True

    proven = verification_claim_annotation(
        "Candidate",
        "This lemma is proven.",
        status="PROVEN",
        metadata={"evidence": {"kind": "FORMAL_PROOF"}},
    )
    assert proven["verification_claim_supported"] is True


def test_revision_record_binds_claim_status_and_evidence():
    record = revision_record(
        revision=2,
        title="R2",
        claim="new claim",
        status="OPEN",
        created_at="now",
        iteration=4,
        metadata={"evidence": {"kind": "EXACT_PASS"}, "strategy": "s"},
    )
    assert record["revision"] == 2
    assert record["claim"] == "new claim"
    assert record["claim_hash"]
    assert record["evidence"]["kind"] == "EXACT_PASS"
    assert record["strategy"] == "s"


def test_revision_boundary_drops_old_proof_and_evidence():
    cleaned = clear_revision_bound_metadata(
        {
            "target_id": "T1",
            "claim_role": "SUBCLAIM",
            "evidence": {"kind": "FORMAL_PROOF"},
            "proof_seal": "old",
            "formal_verified": True,
            "strategy": "next",
        }
    )
    assert cleaned["target_id"] == "T1"
    assert cleaned["claim_role"] == "SUBCLAIM"
    assert cleaned["strategy"] == "next"
    assert "evidence" not in cleaned
    assert "proof_seal" not in cleaned
    assert "formal_verified" not in cleaned


def test_tool_artifact_is_immutable_and_hashed(tmp_path: Path):
    first = persist_tool_artifact(
        tmp_path,
        item_id="C-1",
        revision=1,
        tool_request={"tool": "lean_draft", "source": "theorem a : True := by trivial", "theorem_name": "a"},
    )
    second = persist_tool_artifact(
        tmp_path,
        item_id="C-1",
        revision=1,
        tool_request={"tool": "lean_draft", "source": "theorem b : True := by trivial", "theorem_name": "b"},
    )
    assert first["artifact_path"] == "formal/C-1/r1.lean"
    assert first["sha256"]
    assert second["artifact_path"] != first["artifact_path"]
    assert (tmp_path / first["artifact_path"]).read_text(encoding="utf-8").startswith("theorem a")
    assert (tmp_path / second["artifact_path"]).read_text(encoding="utf-8").startswith("theorem b")
