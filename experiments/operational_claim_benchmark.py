"""Offline safety benchmark; no API key, model call, or discovery claim.

Run from the repository: python experiments/operational_claim_benchmark.py
"""
from __future__ import annotations

import json

from lab.claim_check import check_claim, spec_hash


def main() -> int:
    arithmetic = {"variables": {"n": {"min": 2, "max": 100}}, "assumptions": [],
                  "predicate": "n*(n+1)%2 == 0"}
    original = {"variables": {"n": {"min": 2, "max": None}, "k": {"min": 1, "max": 8}},
                "assumptions": [], "predicate": "collatz_steps(n)>=collatz_prefix_lower(residue(n,k),k)"}
    cases = [
        ("consecutive_product", arithmetic, {}, "EXACT_PASS"),
        ("exact_rational_identity", {**arithmetic, "predicate": "n/3+n/3+n/3 == n"}, {}, "EXACT_PASS"),
        ("polynomial_false_claim", {**arithmetic, "predicate": "(n*n+n+41)%41 != 0"}, {}, "DETERMINISTIC_COUNTEREXAMPLE"),
        ("original_collatz_claim", original, {"scope": {"n": {"min": 2, "max": 60}, "k": {"min": 1, "max": 4}}}, "EXACT_PASS"),
        ("wrong_refutation_rejected", original, {"witness": {"n": 4, "k": 2}}, "INCONCLUSIVE"),
        ("stronger_claim_really_false", {**original, "predicate": "collatz_steps(n)>k"},
         {"witness": {"n": 4, "k": 2}}, "DETERMINISTIC_COUNTEREXAMPLE"),
        ("out_of_domain_witness", arithmetic, {"witness": {"n": 1}}, "INCONCLUSIVE"),
        ("empty_admissible_set", {**arithmetic, "assumptions": ["n<0"]}, {}, "INCONCLUSIVE"),
    ]
    rows = []
    for name, spec, request, expected in cases:
        result = check_claim(spec, request, item_id=name, claim_hash="benchmark", iteration=1)
        actual = result.metadata.get("kind")
        rows.append({"case": name, "expected": expected, "actual": actual, "pass": actual == expected,
                     "spec_hash": spec_hash(spec), "witness": result.metadata.get("witness"), "error": result.error})
    print(json.dumps({"benchmark": "operational_claims_v1", "model_calls": 0,
                      "novelty_assessed": False, "cases": rows}, ensure_ascii=False, indent=2))
    return 0 if all(row["pass"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
