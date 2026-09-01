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
    if tool_result.tool == "code_experiment":
        evidence = dict(tool_result.metadata or {}).get("evidence") or {}
        try:
            return int(evidence.get("successful_run_count", 0)) > 0
        except Exception:
            return False
    if tool_result.tool == "tropical_grid":
        return str((tool_result.metadata or {}).get("status") or "").upper() == "GRID_PASS"
    if tool_result.tool == "z3":
        return str((tool_result.metadata or {}).get("result") or "").lower() in {"sat", "unsat"}
    if tool_result.tool == "script":
        return True
    return False


def choose_status(
    requested: str,
    *,
    tool_result: ToolResult | None,
    verifier: dict[str, Any],
    critic: dict[str, Any],
) -> GuardDecision:
    """Return the strongest status justified by machine-observable evidence."""

    requested = str(requested or "OPEN").upper()
    verifier_verdict = str(verifier.get("verdict") or "INCONCLUSIVE").upper()
    critic_verdict = str(critic.get("verdict") or "REVISE").upper()
    formal_verified = bool(
        tool_result
        and tool_result.ok
        and tool_result.tool == "lean"
        and (tool_result.metadata or {}).get("formal_verified") is True
    )
    computation_ok = _tool_is_successful_computation(tool_result)
    deterministic_counterexample = bool(
        tool_result
        and tool_result.tool == "tropical_grid"
        and str((tool_result.metadata or {}).get("status") or "").upper() == "COUNTEREXAMPLE"
    )
    verifier_counterexample = verifier_verdict == "FAIL" and bool(str(verifier.get("counterexample") or "").strip())

    metadata = {
        "formal_verified": formal_verified,
        "computation_ok": computation_ok,
        "verifier_verdict": verifier_verdict,
        "critic_verdict": critic_verdict,
        "deterministic_counterexample": deterministic_counterexample,
        "verifier_counterexample": verifier_counterexample,
    }

    if deterministic_counterexample or verifier_counterexample:
        return GuardDecision(requested, "FAIL", "Counterexample evidence forces FAIL.", requested != "FAIL", metadata)

    if requested == "PROVEN":
        if formal_verified and verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(
                requested,
                "PROVEN",
                "Formal checker succeeded, verifier passed the candidate, and critic did not KILL it.",
                False,
                metadata,
            )
        return GuardDecision(
            requested,
            "OPEN",
            "PROVEN rejected: requires successful formal checker + verifier PASS + critic not KILL.",
            True,
            metadata,
        )

    if requested == "PROOF_CANDIDATE":
        if verifier_verdict == "PASS" and critic_verdict != "KILL":
            return GuardDecision(requested, "PROOF_CANDIDATE", "Verifier PASS and critic did not KILL.", False, metadata)
        return GuardDecision(requested, "OPEN", "PROOF_CANDIDATE rejected by verifier/critic guard.", True, metadata)

    if requested == "COMPUTATION_PASS":
        if computation_ok:
            return GuardDecision(requested, "COMPUTATION_PASS", "Successful deterministic computation evidence exists.", False, metadata)
        return GuardDecision(requested, "OPEN", "COMPUTATION_PASS rejected: no successful deterministic tool evidence.", True, metadata)

    if requested == "FAIL":
        if critic_verdict == "KILL" or verifier_verdict == "FAIL":
            return GuardDecision(
                requested,
                "DROPPED",
                "No concrete counterexample payload; recorded as DROPPED rather than mathematical FAIL.",
                True,
                metadata,
            )
        return GuardDecision(requested, "OPEN", "FAIL rejected: insufficient failure evidence.", True, metadata)

    if requested == "DROPPED":
        return GuardDecision(requested, "DROPPED", "Manager explicitly closed the research direction.", False, metadata)

    return GuardDecision(requested, "OPEN", "OPEN is always admissible.", requested != "OPEN", metadata)
