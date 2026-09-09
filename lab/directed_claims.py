"""Consolidate classifications by claim identity, never by counting votes."""

from __future__ import annotations

from copy import deepcopy
import json


def consolidate_claims(contract, results: dict, state: str) -> dict:
    groups: dict[str, list] = {}
    for lane_id, result in sorted(results.items()):
        for claim in result.get("claim_results", []):
            groups.setdefault(claim["claim_id"], []).append((lane_id, result, claim))
    primary = contract.primary_claim
    groups.setdefault(primary.claim_id, [])
    claims = []
    for claim_id, entries in sorted(groups.items()):
        # A completed designated auditor is a presentation source, not an override
        # for conflicting evidence. The full lane receipts remain immutable.
        ordered = sorted(
            entries,
            key=lambda e: (not (e[1].get("status") == "COMPLETED" and "auditor" in e[1].get("role", "").lower()), e[0]),
        )
        source = deepcopy(ordered[0][2]) if ordered else {}
        is_primary = claim_id == primary.claim_id
        statement = primary.statement if is_primary else source["statement"]
        statements = {entry[2]["statement"] for entry in entries}
        if is_primary:
            statements.add(primary.statement)
        statuses = {entry[2]["status"] for entry in entries}
        # OPEN at submission is an unassessed starting point, not contradictory
        # evidence. Previously established classifications are preserved/reviewed.
        if is_primary and primary.status_at_start != "OPEN":
            statuses.add(primary.status_at_start)
        bindings = {
            json.dumps({"domain": entry[2].get("domain"), "assumptions": entry[2].get("assumptions")}, sort_keys=True)
            for entry in entries
        }
        binding_conflict = len(bindings) > 1
        if is_primary:
            for _, _, claim in entries:
                if "domain" in claim and claim["domain"] != primary.quantifiers:
                    binding_conflict = True
                if "assumptions" in claim and claim["assumptions"] != primary.definitions:
                    binding_conflict = True
            # A new established classification must explicitly bind the original
            # domain and assumptions; matching prose/ID alone cannot widen it.
            established = {
                "PROVED",
                "PROVED_WITH_DECLARED_STANDARD_INPUT",
                "FORMALLY_VERIFIED",
                "CERTIFIED_NUMERICAL",
                "NUMERICAL",
                "REFUTED",
            }
            if primary.status_at_start == "OPEN" and statuses & established:
                binding_conflict |= any(
                    claim.get("domain") != primary.quantifiers or claim.get("assumptions") != primary.definitions
                    for _, _, claim in entries
                    if claim["status"] in established
                )
        conflict = len(statements) > 1 or len(statuses) > 1 or binding_conflict
        status = "INCONCLUSIVE" if conflict else next(iter(statuses), primary.status_at_start if is_primary else "OPEN")
        missing = source.get("first_missing_step")
        if conflict:
            missing = "Conflicting or unbound domains, assumptions, statements or classifications; principal review required. No majority vote used."
        elif status == "OPEN":
            missing = missing or "No established proof; principal review required"
        if state == "INPUT_INTEGRITY_FAILURE":
            status, missing = "INPUT_INTEGRITY_FAILURE", "Input changed; output unusable"
        evidence = [
            {
                "lane_id": lane_id,
                "lane_status": result.get("status"),
                "status": claim["status"],
                "statement": claim["statement"],
                "domain": claim.get("domain"),
                "assumptions": claim.get("assumptions"),
                "evidence_files": [f"LANES/{lane_id}/EVIDENCE.json", f"LANES/{lane_id}/RESULT.json"],
                "independence_level": "SHARED_TRUST_BASE",
                "algorithm_provenance": result.get("independence_level", "NOT_ASSESSED"),
            }
            for lane_id, result, claim in entries
        ]
        row = {
            "claim_id": claim_id,
            "statement": statement,
            "status": status,
            "domain": primary.quantifiers
            if is_primary
            else source.get("domain", "See frozen definitions and lane evidence"),
            "first_missing_step": missing,
            "classification_conflict": conflict,
            "semantic_binding_conflict": binding_conflict,
            "classification_rule": "RECONCILE_CLASSIFICATIONS_WITHOUT_VOTING",
            "independence_level": "SHARED_TRUST_BASE",
            "supporting_evidence": evidence,
            "evidence_files": sorted({f for item in evidence for f in item["evidence_files"]}),
        }
        if not conflict:
            for key in ("proof", "verification_kind", "assumptions"):
                if key in source:
                    row[key] = source[key]
        claims.append(row)
    return {
        "schema_version": "1.1",
        "authoritative": True,
        "task_id": contract.task_id,
        "primary_claim_id": primary.claim_id,
        "claims": claims,
        "downstream_claims_not_established": contract.scope.excluded,
        "authority_scope": "One consolidated reporting row per claim; not an independent truth certificate",
        "shared_trust_base": ["definitions", "codebase", "coordinator"],
        "mathematical_truth_verified_by_package": False,
    }
