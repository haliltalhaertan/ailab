from __future__ import annotations

import hashlib
import subprocess

from lab.evidence import evidence_from_tool_result
from lab.integrity import content_fingerprint
from lab.status_guard import choose_status
from lab.tools import LeanTool, ToolResult


def _binding(claim: str = "A implies A") -> dict[str, object]:
    return {
        "theorem_name": "bound",
        "theorem_type": "1 = 1",
        "item_id": "C-test",
        "iteration": 1,
        "claim_hash": content_fingerprint("claim:v1", claim),
        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
    }


def test_lean_draft_rejects_sorry(tmp_path):
    tool = LeanTool(tmp_path / "formal")
    result = tool.draft_source(
        "candidate.lean",
        "theorem bound : 1 = 1 := by sorry",
        **_binding(),
    )
    assert not result.ok
    assert result.metadata.get("formal_verified") is False


def test_lean_rc_zero_with_sorry_warning_is_not_formal_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    tool = LeanTool(tmp_path / "formal")
    binding = _binding()
    draft = tool.draft_source(
        "candidate.lean",
        "theorem bound : 1 = 1 := by rfl",
        **binding,
    )
    assert draft.ok

    def fake_run(_candidate):
        return (
            subprocess.CompletedProcess(
                ["lean"],
                0,
                stdout="",
                stderr="warning: declaration uses 'sorry'",
            ),
            "lean",
        )

    monkeypatch.setattr(tool, "_run_lean", fake_run)
    result = tool.check_file(
        "candidate.lean",
        expected_sha256=str(draft.metadata["lean_sha256"]),
        expected_item_id=str(binding["item_id"]),
        expected_iteration=int(binding["iteration"]),
        expected_claim_hash=str(binding["claim_hash"]),
        expected_claim_sha256=str(binding["claim_sha256"]),
        expected_theorem_name=str(binding["theorem_name"]),
        expected_theorem_type=str(binding["theorem_type"]),
    )
    assert not result.ok
    assert result.metadata["formal_verified"] is False


def _formal_result(claim_hash: str) -> ToolResult:
    return ToolResult(
        True,
        "lean",
        metadata={
            "formal_verified": True,
            "source_clean": True,
            "axioms_verified": True,
            "formal_binding_verified": True,
            "item_id": "C-test",
            "iteration": 1,
            "claim_hash": claim_hash,
        },
    )


def _bound_formal_evidence(result: ToolResult, claim_hash: str, revision: int = 1):
    return evidence_from_tool_result(
        result,
        claim_hash=claim_hash,
        revision=revision,
    )


def test_claim_hash_mismatch_cannot_be_proven():
    expected = content_fingerprint("claim:v1", "the actual claim")
    result = _formal_result(content_fingerprint("claim:v1", "a different claim"))
    decision = choose_status(
        "PROVEN",
        tool_result=result,
        verifier={"verdict": "PASS"},
        critic={"verdict": "KEEP"},
        expected_item_id="C-test",
        expected_iteration=1,
        expected_claim_hash=expected,
        expected_revision=1,
        evidence=_bound_formal_evidence(result, expected, 1),
    )
    assert decision.granted == "OPEN"
    assert decision.metadata["claim_hash_matches"] is False


def test_unbound_evidence_is_not_bound_by_guard():
    expected = content_fingerprint("claim:v1", "the actual claim")
    result = _formal_result(expected)
    unbound = evidence_from_tool_result(result)

    decision = choose_status(
        "PROVEN",
        tool_result=result,
        verifier={"verdict": "PASS"},
        critic={"verdict": "KEEP"},
        expected_item_id="C-test",
        expected_iteration=1,
        expected_claim_hash=expected,
        expected_revision=1,
        evidence=unbound,
    )

    assert decision.granted == "OPEN"
    assert decision.metadata["evidence_kind"] == "INCONCLUSIVE"
    assert decision.metadata["actual_evidence_revision"] is None


def test_matching_claim_hash_revision_with_pass_and_keep_is_proven():
    expected = content_fingerprint("claim:v1", "the actual claim")
    result = _formal_result(expected)
    decision = choose_status(
        "PROVEN",
        tool_result=result,
        verifier={"verdict": "PASS"},
        critic={"verdict": "KEEP"},
        expected_item_id="C-test",
        expected_iteration=1,
        expected_claim_hash=expected,
        expected_revision=1,
        evidence=_bound_formal_evidence(result, expected, 1),
    )
    assert decision.granted == "PROVEN"
    assert decision.metadata["claim_hash_matches"] is True
    assert decision.metadata["actual_evidence_revision"] == 1
