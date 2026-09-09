from copy import deepcopy

from lab.directed_claims import consolidate_claims
from lab.directed_contract import DirectedTaskContract
from test_directed import contract


def result(status="PROVED", statement="Known identity"):
    return {
        "status": "COMPLETED",
        "role": "Theorist",
        "independence_level": "distinct_algorithm",
        "claim_results": [
            {"claim_id": "ID", "statement": statement, "status": status, "proof": "Elementary derivation"}
        ],
    }


def test_repeated_claims_are_one_row_with_evidence_not_four_proofs():
    c = DirectedTaskContract.model_validate(contract())
    results = {name: result() for name in ("a", "b", "boundary", "audit")}
    results["audit"]["role"] = "FinalAuditor"
    ledger = consolidate_claims(c, results, "COMPLETED_WITH_OPEN_CLAIMS")
    assert ledger["schema_version"] == "1.1" and ledger["authoritative"]
    assert len({x["claim_id"] for x in ledger["claims"]}) == len(ledger["claims"]) == 2
    claim = next(x for x in ledger["claims"] if x["claim_id"] == "ID")
    assert claim["status"] == "PROVED"
    assert len(claim["supporting_evidence"]) == 4
    assert claim["independence_level"] == "SHARED_TRUST_BASE"
    assert not ledger["mathematical_truth_verified_by_package"]
    assert ledger == consolidate_claims(c, dict(reversed(list(results.items()))), "COMPLETED_WITH_OPEN_CLAIMS")


def test_disagreement_is_not_resolved_by_votes_or_auditor_role():
    c = DirectedTaskContract.model_validate(contract())
    results = {str(i): result() for i in range(4)}
    results["4"] = result("REFUTED")
    results["0"]["role"] = "FinalAuditor"
    row = consolidate_claims(c, results, "PARTIAL")["claims"][0]
    assert row["claim_id"] == "ID" and row["status"] == "INCONCLUSIVE"
    assert row["classification_conflict"] and "proof" not in row


def test_same_id_different_statement_is_not_merged_as_proof():
    c = DirectedTaskContract.model_validate(contract())
    row = consolidate_claims(c, {"a": result(), "b": result(statement="Different identity")}, "PARTIAL")["claims"][0]
    assert row["status"] == "INCONCLUSIVE" and row["classification_conflict"]


def test_primary_appears_once_and_integrity_failure_overrides_every_row():
    c = DirectedTaskContract.model_validate(contract())
    r = result("OPEN", c.primary_claim.statement)
    r["claim_results"][0]["claim_id"] = c.primary_claim.claim_id
    ledger = consolidate_claims(c, {"a": r, "b": deepcopy(r)}, "INPUT_INTEGRITY_FAILURE")
    assert len(ledger["claims"]) == 1
    assert ledger["claims"][0]["status"] == "INPUT_INTEGRITY_FAILURE"


def test_same_statement_narrow_domain_cannot_promote_primary():
    c = DirectedTaskContract.model_validate(contract())
    r = result("PROVED", c.primary_claim.statement)
    claim = r["claim_results"][0]
    claim.update(claim_id=c.primary_claim.claim_id, domain="x=0 only", assumptions=c.primary_claim.definitions)
    row = consolidate_claims(c, {"audit": r}, "COMPLETED")["claims"][0]
    assert row["status"] == "INCONCLUSIVE" and row["semantic_binding_conflict"]
    assert row["supporting_evidence"][0]["domain"] == "x=0 only"
    assert "proof" not in row


def test_different_assumptions_require_review_even_with_same_status():
    c = DirectedTaskContract.model_validate(contract())
    a, b = result(), result()
    a["claim_results"][0]["assumptions"] = ["x positive"]
    b["claim_results"][0]["assumptions"] = ["x negative"]
    row = consolidate_claims(c, {"a": a, "b": b}, "COMPLETED")["claims"][0]
    assert row["status"] == "INCONCLUSIVE" and row["semantic_binding_conflict"]
