from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lab.evidence import Evidence, evidence_from_tool_result, validate_evidence_binding
from lab.research_contract import ResearchContract
from lab.tools import ToolResult


@dataclass(frozen=True)
class GuardDecision:
    requested: str
    granted: str
    reason: str
    downgraded: bool
    metadata: dict[str, Any]


def choose_status(
    requested: str,
    *,
    tool_result: ToolResult | None,
    verifier: dict[str, Any],
    critic: dict[str, Any],
    expected_item_id: str | None = None,
    expected_iteration: int | None = None,
    expected_claim_hash: str | None = None,
    expected_revision: int | None = None,
    evidence: Evidence | None = None,
    contract: ResearchContract | None = None,
) -> GuardDecision:
    """Return the strongest status justified by machine-observable evidence."""

    requested = str(requested or "OPEN").upper()
    verifier_verdict = str(verifier.get("verdict") or "INCONCLUSIVE").upper()
    critic_verdict = str(critic.get("verdict") or "REVISE").upper()
    tmeta = dict((tool_result.metadata if tool_result else None) or {})
    bound_evidence = evidence
    if bound_evidence is None and tool_result is not None:
        bound_evidence = evidence_from_tool_result(tool_result, contract=contract)
    if bound_evidence is not None:
        bound_evidence = validate_evidence_binding(
            bound_evidence,
            contract=contract,
            expected_claim_hash=expected_claim_hash,
            expected_revision=expected_revision,
        )

    claim_hash_matches = bool(
        expected_claim_hash
        and str(tmeta.get("claim_hash") or "") == str(expected_claim_hash)
    )
    formal_verified = bool(
        tool_result
        and tool_result.ok
        and tool_result.tool == "lean"
        and tmeta.get("formal_verified") is True
        and tmeta.get("source_clean") is True
        and tmeta.get("axioms_verified") is True
        and tmeta.get("formal_binding_verified") is True
        and claim_hash_matches
        and (expected_item_id is None or str(tmeta.get("item_id") or "") == str(expected_item_id))
        and (expected_iteration is None or int(tmeta.get("iteration", -1)) == int(expected_iteration))
        and (bound_evidence is None or bound_evidence.kind != "INCONCLUSIVE")
    )
    evidence_kind = bound_evidence.kind if bound_evidence is not None else "INCONCLUSIVE"
    computation_ok = bool(
        bound_evidence
        and bound_evidence.ok
        and evidence_kind
        in {
            "EXACT_PASS",
            "EXHAUSTIVE_NO_SOLUTION",
            "EXHAUSTIVE_OPTIMUM",
            "NUMERICAL_PASS",
        }
    )
    deterministic_counterexample = bool(
        bound_evidence
        and bound_evidence.ok
        and evidence_kind == "DETERMINISTIC_COUNTEREXAMPLE"
        and bound_evidence.witness is not None
    )

    verifier_counterexample = str(verifier.get("counterexample") or "").strip()
    critic_counterexample = str(critic.get("counterexample") or "").strip()
    llm_counterexample = verifier_counterexample or critic_counterexample
    llm_refutation_candidate = bool(llm_counterexample) and not deterministic_counterexample

    metadata = {
        "formal_verified": formal_verified,
        "formal_binding_verified": bool(tmeta.get("formal_binding_verified")),
        "axioms_verified": bool(tmeta.get("axioms_verified")),
        "source_clean": bool(tmeta.get("source_clean")),
        "claim_hash_matches": claim_hash_matches,
        "expected_claim_hash": expected_claim_hash,
        "actual_claim_hash": str(tmeta.get("claim_hash") or ""),
        "expected_revision": expected_revision,
        "actual_evidence_revision": bound_evidence.revision if bound_evidence else None,
        "evidence_claim_hash": bound_evidence.claim_hash if bound_evidence else "",
        "computation_ok": computation_ok,
        "verifier_verdict": verifier_verdict,
        "critic_verdict": critic_verdict,
        "deterministic_counterexample": deterministic_counterexample,
        "deterministic_counterexample_type": evidence_kind if deterministic_counterexample else "",
        "llm_counterexample": llm_counterexample,
        "llm_refutation_candidate": llm_refutation_candidate,
        "expected_item_id": expected_item_id,
        "expected_iteration": expected_iteration,
        "evidence_kind": evidence_kind,
        "evidence_hash": bound_evidence.evidence_hash if bound_evidence else "",
        "source_origin": bound_evidence.source_origin if bound_evidence else "",
        "evidence_role": bound_evidence.evidence_role if bound_evidence else "",
        "resolution_scope": bound_evidence.resolution_scope if bound_evidence else "",
    }

    if deterministic_counterexample:
        return GuardDecision(requested, "FAIL", "Deterministically verified counterexample evidence forces FAIL.", requested != "FAIL", metadata)

    if llm_refutation_candidate:
        return GuardDecision(
            requested,
            "REFUTATION_CANDIDATE",
            "LLM-only counterexample is not deterministic evidence; keep it active until verified.",
            requested != "REFUTATION_CANDIDATE",
            metadata,
        )

    if requested == "PROVEN":
        if formal_verified and verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(
                requested,
                "PROVEN",
                "Bound Lean source passed the checker, claim-hash/revision/source/axiom guards, verifier PASS, and critic did not KILL it.",
                False,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "PROVEN rejected: requires current-revision same-item/same-iteration/same-claim bound Lean evidence, clean source, verified axioms, verifier PASS, and critic not KILL.",
            True,
            metadata,
        )

    if requested == "PROOF_CANDIDATE":
        if verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(requested, "PROOF_CANDIDATE", "Verifier PASS and critic did not KILL.", False, metadata)
        return GuardDecision(requested, "OPEN", "PROOF_CANDIDATE rejected by verifier/critic guard.", True, metadata)

    if requested == "COMPUTATION_PASS":
        if computation_ok:
            return GuardDecision(requested, "COMPUTATION_PASS", "Current-revision bound machine evidence justifies computation status.", False, metadata)
        return GuardDecision(
            requested,
            "OPEN",
            f"COMPUTATION_PASS rejected: evidence kind {evidence_kind} is not sufficient for the current claim revision.",
            True,
            metadata,
        )

    if requested == "FAIL":
        if critic_verdict == "KILL" or verifier_verdict == "FAIL":
            return GuardDecision(
                requested,
                "DROPPED",
                "No deterministic counterexample exists; negative LLM judgement alone cannot create mathematical FAIL.",
                True,
                metadata,
            )
        return GuardDecision(requested, "OPEN", "FAIL rejected: insufficient deterministic failure evidence.", True, metadata)

    if requested == "REFUTATION_CANDIDATE":
        return GuardDecision(
            requested,
            "REFUTATION_CANDIDATE",
            "Refutation candidate remains active pending deterministic verification.",
            False,
            metadata,
        )

    if requested == "DROPPED":
        return GuardDecision(requested, "DROPPED", "Manager explicitly closed the research direction.", False, metadata)

    return GuardDecision(requested, "OPEN", "OPEN is always admissible.", requested != "OPEN", metadata)
