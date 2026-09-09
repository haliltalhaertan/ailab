"""Closed, reviewed small-b algebra recipes. Never executes supplied expressions.

Certificates check finite integer Laurent polynomials; the accompanying proofs
declare their elementary real/complex analysis inputs. This is not a Lean proof
checker, discovery oracle, or a verifier for arbitrary user statements.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from typing import Any

DEFINITIONS = {
    "alpha": "log(3)/log(2)",
    "beta": "log(3)/log(2)-1",
    "q": "q(x)=exp(2*pi*i*2^x)",
    "z": "z=2^(x-beta)>0",
    "A": "A_b(x)=q(x)/(b+1)*sum(q(x+u-beta),u=0..b)",
    "domain": "x real; b integer >=0; pilot b in {0,1,2,3,4}",
}
CLAIMS = {
    "PILOT-A0": "|A_0(x)|=1 for every real x",
    "PILOT-A1": "|A_1(x)|=|cos(pi*2^(x-beta))|",
    "PILOT-GENERAL-MODULUS": "The stated exact pairwise-cosine modulus identity holds",
    "PILOT-EQUALITY": "For b>=1, |A_b(x)|=1 iff 2^(x-beta) is an integer",
    "PILOT-XUB": "The small-b identities imply XUB",
}
STANDARD_INPUTS = [
    "Real exponent laws; beta=log_2(3)-1 implies 2^beta=3/2.",
    "For real t: conjugate(exp(i*t))=exp(-i*t), |exp(i*t)|=1.",
    "Euler cosine identity, 1-cos(2*t)=2*sin(t)^2.",
    "For real t: exp(2*pi*i*t)=1 iff t is an integer.",
    "Finite sums of nonnegative reals vanish iff each term vanishes.",
    "|exp(i*t)-exp(i*s)| <= |t-s|; Archimedean property of reals.",
]
PROOFS = {
    "change_of_variable": "2^beta=3/2 gives 2^x=3*z/2, hence q(x)=exp(3*pi*i*z) and q(x+u-beta)=exp(2*pi*i*2^u*z). Thus A_b is exp(3*pi*i*z) times the normalized lacunary sum. This is not a constant-ratio geometric series for b>=2.",
    "PILOT-A0": "At b=0 the product is exp(3*pi*i*z)*exp(2*pi*i*z)=exp(5*pi*i*z). Its modulus is one for real z.",
    "PILOT-A1": "exp(2*pi*i*z)+exp(4*pi*i*z)=exp(3*pi*i*z)*(exp(-pi*i*z)+exp(pi*i*z))=2*exp(3*pi*i*z)*cos(pi*z). Multiplying by exp(3*pi*i*z)/2 gives A_1=exp(6*pi*i*z)*cos(pi*z). Taking modulus proves the statement.",
    "PILOT-GENERAL-MODULUS": "For every finite integer b>=0, expand the product of the sum and its conjugate. The b+1 diagonal terms are one; off-diagonal terms indexed u<v pair into 2*cos(2*pi*(2^v-2^u)*z). Divide by (b+1)^2. Subtract from one, use (b+1)^2-(b+1)=2*binom(b+1,2), then 1-cos(2*t)=2*sin(t)^2. Therefore 1-|A_b|^2=4/(b+1)^2*sum_{u<v}sin^2(pi*(2^v-2^u)*z). The coefficient certificates independently verify b=0..4; this finite-sum argument gives the general identity.",
    "PILOT-EQUALITY": "For b>=1 the exact gap is a sum of nonnegative squares. Gap zero forces its u=0,v=1 summand sin^2(pi*z)=0, hence z is an integer. Conversely integer z makes every phase exp(2*pi*i*2^u*z)=1, so the normalized sum has modulus one. Noninteger positive z gives positive gap and strict pointwise contraction. Since z>0, the attainable equality integers are positive.",
    "pair_state": "x'=x+b-2*beta gives z'=2^(x'-beta)=2^(b-2*beta)*z=(2^(b+2)/9)*z.",
    "no_uniform_gap": "For fixed b>=1 take z_N=1+1/N, N>=2. These are positive nonintegers. With f_u=2^u, the exponential Lipschitz inequality gives |S_b(z_N)-1|<=2*pi*sum(f_u)/((b+1)*N). Hence |S_b(z_N)|>=1-2*pi*(2^(b+1)-1)/((b+1)*N), which approaches one. There is no uniform contraction factor below one on all noninteger positive z. At b=0 modulus is identically one.",
    "PILOT-XUB": "OPEN: the identities alone supply no occupation/crossing estimate, asymptotic profile or XUB proof. No downstream theorem is certified and no continuation is launched.",
}


def recipe(variant: str) -> dict[str, Any]:
    """Make explicit input bytes to seal with the task contract."""
    return {"recipe": "small_b_v1", "variant": variant,
            "definitions": deepcopy(DEFINITIONS), "claims": deepcopy(CLAIMS)}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _route_a() -> list[dict[str, Any]]:
    # Pair enumeration, separating diagonal and positive/negative frequencies.
    result = []
    for b in range(5):
        counts: Counter[int] = Counter({0: b + 1})
        for u in range(b + 1):
            for v in range(u + 1, b + 1):
                d = 2**v - 2**u
                counts[d] += 1
                counts[-d] += 1
        result.append({"b": b, "denominator": (b + 1)**2,
                       "laurent_coefficients": {str(k): counts[k] for k in sorted(counts)}})
    return result


def _route_b() -> list[dict[str, Any]]:
    # Independent dense polynomial multiplication, followed by exponent shift.
    output = []
    for size in range(1, 6):
        degree = 1 << (size - 1)
        polynomial = [0] * (degree + 1)
        position = 1
        for _ in range(size):
            polynomial[position] = 1
            position *= 2
        reciprocal = polynomial[::-1]
        product = [0] * (2 * degree + 1)
        for i, left in enumerate(polynomial):
            for j, right in enumerate(reciprocal):
                product[i + j] += left * right
        output.append({"b": size - 1, "denominator": size * size,
                       "laurent_coefficients": {str(index - degree): coefficient
                                                for index, coefficient in enumerate(product) if coefficient}})
    return output


def _boundaries() -> dict[str, Any]:
    # All frequencies are integers; at z=1/2 exactly the frequency 1 has phase -1.
    return {
        "integer_z": "At every positive integer z all phases equal 1, for every b>=0.",
        "half_integer_squared_moduli": [str(Fraction((b - 1)**2, (b + 1)**2)) for b in range(5)],
        "b_zero": "b=0 has modulus one even at noninteger z; equality theorem must require b>=1.",
        "near_integer": PROOFS["no_uniform_gap"],
        "domain": "z=0 and negative z are excluded by z=2^(x-beta)>0; no floating point phase evaluation used.",
        "state_multipliers": [str(Fraction(2**(b + 2), 9)) for b in range(5)],
        "forbidden_escalations": ["XUB", "uniform white occupation", "O(1/m) crossing", "E6-N2", "E7-B4", "nonzero asymptotic profile", "polynomial lower bound", "Collatz conjecture"],
    }


def execute_recipe(raw: dict, dependency_public: dict) -> dict:
    """Reject arbitrary definitions/statements; run a versioned, bounded recipe.

    Audit dependency values must be complete prior public outputs returned here.
    Exact equality checks and local recomputation detect modified certificates.
    Engine remains responsible for dependency provenance and sealed-file hashes.
    """
    variant = raw.get("variant")
    if variant not in {"derive_a", "derive_b", "boundary", "audit"} or raw != recipe(variant):
        raise ValueError("Unsupported or modified small-b recipe, definitions, or claim statements")
    if variant != "audit" and dependency_public:
        raise ValueError("Independent pilot producer/boundary recipes must not receive other lane outputs")
    coefficients = _route_b() if variant == "derive_b" else _route_a()
    if variant == "audit":
        required = {"derive_a", "derive_b", "boundary"}
        seen = set()
        for candidate in dependency_public.values():
            if not isinstance(candidate, dict):
                raise ValueError("Audit requires complete structured public certificates")
            source_variant = candidate.get("evidence", {}).get("variant")
            if source_variant not in required or source_variant in seen:
                raise ValueError("Audit needs exactly one certificate from each independent route and boundary")
            if candidate != execute_recipe(recipe(source_variant), {}):
                raise ValueError("Audit dependency differs from independently recomputed certificate")
            seen.add(source_variant)
        if seen != required or _route_a() != _route_b():
            raise ValueError("Missing independent derivation/boundary, or coefficient disagreement")
    claim_results = [{"claim_id": key, "statement": statement,
                      "status": "OPEN" if key == "PILOT-XUB" else "PROVED",
                      "proof": PROOFS[key], "verification_kind": "reviewed_elementary_algebra_not_formal_kernel"}
                     for key, statement in CLAIMS.items()]
    return {
        "public_findings": {"definitions": deepcopy(DEFINITIONS), "proofs": deepcopy(PROOFS),
                            "standard_inputs": list(STANDARD_INPUTS), "boundary_checks": _boundaries()},
        "machine_status": "PROVED", "claim_results": claim_results,
        "evidence": {"recipe": "small_b_v1", "variant": variant,
                     "input_sha256": _hash(raw), "coefficient_certificate": coefficients,
                     "formal_kernel_verified": False,
                     "scope": "Elementary identities only; finite coefficient checks b=0..4, explicit general finite-sum proof."},
        "missing_steps": ["No XUB, crossing, occupation, asymptotic, novelty or Collatz proof.",
                          "No external formal proof kernel verification."],
        "independence": {"kind": "distinct_deterministic_algorithms_not_independent_model_derivations",
                         "independence_level": "shared_codebase_distinct_algorithms",
                         "shared_inputs": "Exact sealed definitions and reviewed elementary identities",
                         "other_lane_outputs_read": variant == "audit",
                         "audit_recomputed_dependencies": variant == "audit"},
    }
