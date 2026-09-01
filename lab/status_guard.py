from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lab.tools import ToolResult


@dataclass(frozen=True)
class GuardDecision:
    requested: str
    granted: str
    reason: str
    downgraded: bool
    metadata: dict[str, Any]


def _tool_is_successful_computation(tool_result: ToolResult | None) -> bool:
    if tool_result is None or not tool_result.ok:
        return False
    metadata = dict(tool_result.metadata or {})
    if tool_result.tool == "code_experiment":
        evidence = metadata.get("evidence") or {}
        try:
            return int(evidence.get("successful_run_count", 0)) > 0
        except Exception:
            return False
    if tool_result.tool == "tropical_grid":
        return (
            str(metadata.get("status") or "").upper() == "GRID_PASS"
            and metadata.get("provenance_structure_ok") is True
        )
    if tool_result.tool == "z3":
        try:
            assertion_count = int(metadata.get("assertion_count", 0) or 0)
        except Exception:
            assertion_count = 0
        return assertion_count > 0 and str(metadata.get("result") or "").lower() in {
            "sat",
            "unsat",
        }
    if tool_result.tool == "script":
        return True
    return False


def choose_status(
    requested: str,
    *,
    tool_result: ToolResult | None,
    verifier: dict[str, Any],
    critic: dict[str, Any],
    expected_item_id: str | None = None,
    expected_iteration: int | None = None,
    expected_claim_hash: str | None = None,
) -> GuardDecision:
    """Return the strongest status justified by machine-observable evidence."""

    requested = str(requested or "OPEN").upper()
    verifier_verdict = str(verifier.get("verdict") or "INCONCLUSIVE").upper()
    critic_verdict = str(critic.get("verdict") or "REVISE").upper()
    tmeta = dict((tool_result.metadata if tool_result else None) or {})

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
        and (
            expected_item_id is None
            or str(tmeta.get("item_id") or "") == str(expected_item_id)
        )
        and (
            expected_iteration is None
            or int(tmeta.get("iteration", -1)) == int(expected_iteration)
        )
    )
    computation_ok = _tool_is_successful_computation(tool_result)
    tropical_status = str(tmeta.get("status") or "").upper()
    deterministic_counterexample = bool(
        tool_result
        and tool_result.tool == "tropical_grid"
        and tropical_status in {"COUNTEREXAMPLE", "STRUCTURE_MISMATCH"}
    )
    verifier_counterexample = verifier_verdict == "FAIL" and bool(
        str(verifier.get("counterexample") or "").strip()
    )

    metadata = {
        "formal_verified": formal_verified,
        "formal_binding_verified": bool(tmeta.get("formal_binding_verified")),
        "axioms_verified": bool(tmeta.get("axioms_verified")),
        "source_clean": bool(tmeta.get("source_clean")),
        "claim_hash_matches": claim_hash_matches,
        "expected_claim_hash": expected_claim_hash,
        "actual_claim_hash": str(tmeta.get("claim_hash") or ""),
        "computation_ok": computation_ok,
        "verifier_verdict": verifier_verdict,
        "critic_verdict": critic_verdict,
        "deterministic_counterexample": deterministic_counterexample,
        "deterministic_counterexample_type": tropical_status
        if deterministic_counterexample
        else "",
        "verifier_counterexample": verifier_counterexample,
        "expected_item_id": expected_item_id,
        "expected_iteration": expected_iteration,
    }

    if deterministic_counterexample or verifier_counterexample:
        return GuardDecision(
            requested,
            "FAIL",
            "Counterexample/structural refutation evidence forces FAIL.",
            requested != "FAIL",
            metadata,
        )

    if requested == "PROVEN":
        if formal_verified and verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(
                requested,
                "PROVEN",
                "Bound Lean source passed the checker, claim-hash/source/axiom guards, verifier PASS, and critic did not KILL it.",
                False,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "PROVEN rejected: requires same-item/same-iteration/same-claim bound Lean evidence, clean source, verified axioms, verifier PASS, and critic not KILL.",
            True,
            metadata,
        )

    if requested == "PROOF_CANDIDATE":
        if verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(
                requested,
                "PROOF_CANDIDATE",
                "Verifier PASS and critic did not KILL.",
                False,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "PROOF_CANDIDATE rejected by verifier/critic guard.",
            True,
            metadata,
        )

    if requested == "COMPUTATION_PASS":
        if computation_ok:
            return GuardDecision(
                requested,
                "COMPUTATION_PASS",
                "Successful non-empty deterministic computation evidence exists.",
                False,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "COMPUTATION_PASS rejected: no meaningful successful deterministic tool evidence.",
            True,
            metadata,
        )

    if requested == "FAIL":
        if critic_verdict == "KILL" or verifier_verdict == "FAIL":
            return GuardDecision(
                requested,
                "DROPPED",
                "No concrete counterexample payload; recorded as DROPPED rather than mathematical FAIL.",
                True,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "FAIL rejected: insufficient failure evidence.",
            True,
            metadata,
        )

    if requested == "DROPPED":
        return GuardDecision(
            requested,
            "DROPPED",
            "Manager explicitly closed the research direction.",
            False,
            metadata,
        )

    return GuardDecision(
        requested,
        "OPEN",
        "OPEN is always admissible.",
        requested != "OPEN",
        metadata,
    )
