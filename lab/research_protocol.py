from __future__ import annotations

import json
from typing import Any

from lab.completion_integrity import load_bearing_structured_incomplete
from lab.research_contract import ResearchContract, TargetTransition
from lab.research_state import ResearchState


PILOT_EVIDENCE_KINDS = {
    "EXACT_PASS",
    "EXHAUSTIVE_NO_SOLUTION",
    "EXHAUSTIVE_OPTIMUM",
    "DETERMINISTIC_COUNTEREXAMPLE",
}
PILOT_SOURCE_ORIGINS = {"BUILTIN", "CHECKED_IN"}


def ledger_records(state: ResearchState) -> list[dict[str, Any]]:
    """Return machine-authored records used by target transition gates.

    Conjectures retain their code-assigned ``claim_role``. Deterministic pilot
    experiments are included without fabricating one; the contract evaluator
    may treat full-scope experiment evidence as target-resolution evidence for
    COMPUTE/OPTIMIZE targets.
    """

    records: list[dict[str, Any]] = []
    for kind in ("conjecture", "experiment"):
        for item in state.list_items(kind=kind):
            raw_evidence = item.metadata.get("evidence")
            records.append(
                {
                    "item_id": item.id,
                    "record_kind": kind,
                    "claim_role": str(item.metadata.get("claim_role") or ""),
                    "status": item.status,
                    "pilot": bool(item.metadata.get("pilot")),
                    "evidence": dict(raw_evidence) if isinstance(raw_evidence, dict) else None,
                }
            )
    return records


def pilot_evidence_by_target(
    contract: ResearchContract,
    state: ResearchState,
) -> dict[str, list[dict[str, Any]]]:
    """Return strong bound pilot evidence, including evidence for resolved targets."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in state.list_items():
        raw = item.metadata.get("evidence")
        if not isinstance(raw, dict):
            continue
        evidence = dict(raw)
        target_id = str(evidence.get("target_id") or "")
        if not target_id:
            continue
        try:
            target = contract.target(target_id)
        except KeyError:
            continue
        if str(evidence.get("contract_hash") or "") != contract.contract_hash:
            continue
        if str(evidence.get("target_hash") or "") != target.target_hash:
            continue
        if str(evidence.get("kind") or "") not in PILOT_EVIDENCE_KINDS:
            continue
        if str(evidence.get("termination_reason") or "") != "completed":
            continue
        if str(evidence.get("source_origin") or "") not in PILOT_SOURCE_ORIGINS:
            continue
        if not bool(evidence.get("ok")):
            continue
        grouped.setdefault(target_id, []).append(evidence)
    return grouped


def selectable_target_ids(
    contract: ResearchContract,
    pilot_evidence: dict[str, list[dict[str, Any]]],
    *,
    allow_discovery_without_pilot: bool = False,
) -> list[str]:
    open_ids = contract.open_target_ids()
    if contract.pilot_policy == "REQUIRED" and not allow_discovery_without_pilot:
        return [target_id for target_id in open_ids if pilot_evidence.get(target_id)]
    return open_ids


def pilot_prompt_block(
    contract: ResearchContract,
    pilot_evidence: dict[str, list[dict[str, Any]]],
) -> str:
    lines = ["\n\n--- DETERMINISTIC PILOT EVIDENCE ---"]
    found = False
    for target in contract.open_targets:
        if target.status != "OPEN":
            continue
        for evidence in pilot_evidence.get(target.id, []):
            found = True
            metadata = dict(evidence.get("metadata") or {})
            witness = evidence.get("witness")
            lines.append(
                f"- {target.id} [{target.target_type}] {target.statement} :: "
                f"kind={evidence.get('kind')} exhaustive={evidence.get('exhaustive')} "
                f"tool_sha256={evidence.get('tool_sha256')} witness={json.dumps(witness, ensure_ascii=False)} "
                f"candidate_sha256={metadata.get('candidate_sha256', '')}"
            )
    if not found:
        lines.append("- (none)")
    lines.append("--- END DETERMINISTIC PILOT EVIDENCE ---")
    return "\n".join(lines)


def evaluate_manager_target_proposal(
    contract: ResearchContract,
    state: ResearchState,
    manager_decision: dict[str, Any],
) -> tuple[bool, str, dict[str, Any] | None]:
    """Apply a manager-proposed transition only when code-side gates permit it."""

    incomplete = load_bearing_structured_incomplete()
    if any(incomplete.values()):
        return False, "target transition rejected: current load-bearing structured output is incomplete", {
            "completion_integrity": incomplete
        }

    raw = manager_decision.get("target_proposal")
    if not isinstance(raw, dict) or not any(str(value or "").strip() for value in raw.values()):
        return False, "no target transition proposed", None
    target_id = str(raw.get("target_id") or "").strip()
    requested = str(raw.get("status") or "").upper()
    if not target_id or requested not in {"CLOSED", "FAILED", "SUPERSEDED"}:
        return False, "invalid target transition proposal", dict(raw)
    try:
        contract.target(target_id, require_open=True)
    except (KeyError, ValueError):
        return False, "target transition references a missing or non-OPEN target", dict(raw)

    if requested == "SUPERSEDED":
        replacement = str(raw.get("superseded_by") or "").strip()
        try:
            updated = contract.supersede_target(target_id, replacement)
        except (KeyError, ValueError) as exc:
            return False, str(exc), dict(raw)
        contract.save(state.root)
        return True, "target superseded by newer OPEN target", {
            "target_id": updated.id,
            "status": updated.status,
            "superseded_by": updated.superseded_by,
        }

    try:
        transition = contract.evaluate_target_transition(target_id, ledger_records(state))
    except (KeyError, ValueError) as exc:
        return False, str(exc), dict(raw)
    if transition is None:
        return False, "machine evidence gate for target transition is not satisfied", dict(raw)
    if transition.status != requested:
        return False, f"machine gate permits {transition.status}, not requested {requested}", dict(raw)

    updated = contract.apply_target_transition(target_id, transition)
    contract.save(state.root)
    return True, transition.reason, {
        "target_id": updated.id,
        "status": updated.status,
        "closed_by": list(updated.closed_by),
        "metadata": dict(updated.metadata),
    }


def human_close_discover_target(
    contract: ResearchContract,
    state: ResearchState,
    target_id: str,
) -> TargetTransition:
    target = contract.target(target_id, require_open=True)
    if target.target_type != "DISCOVER":
        raise ValueError("human close helper is only for DISCOVER targets")
    transition = contract.evaluate_target_transition(target_id, ledger_records(state), human_approved=True)
    if transition is None:
        raise ValueError("DISCOVER target could not be closed")
    contract.apply_target_transition(target_id, transition)
    contract.save(state.root)
    return transition
