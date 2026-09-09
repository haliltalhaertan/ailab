"""Offline tests for bounded trusted elementary-algebra recipes."""
import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "lab" / "directed_algebra.py"
_SPEC = importlib.util.spec_from_file_location("pilot_algebra_tested", _PATH)
assert _SPEC and _SPEC.loader
algebra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(algebra)


def outputs():
    return {v: algebra.execute_recipe(algebra.recipe(v), {})
            for v in ("derive_a", "derive_b", "boundary")}


def test_distinct_routes_agree_with_hand_coefficients():
    a = algebra.execute_recipe(algebra.recipe("derive_a"), {})
    b = algebra.execute_recipe(algebra.recipe("derive_b"), {})
    assert a["evidence"]["coefficient_certificate"] == b["evidence"]["coefficient_certificate"]
    certificate = a["evidence"]["coefficient_certificate"][2]
    assert certificate == {"b": 2, "denominator": 9, "laurent_coefficients":
                           {"-3": 1, "-2": 1, "-1": 1, "0": 3, "1": 1, "2": 1, "3": 1}}
    assert not a["independence"]["other_lane_outputs_read"]


def test_audit_scopes_proved_and_open_claims():
    result = algebra.execute_recipe(algebra.recipe("audit"), outputs())
    statuses = {item["claim_id"]: item["status"] for item in result["claim_results"]}
    assert statuses == {key: "OPEN" if key == "PILOT-XUB" else "PROVED" for key in algebra.CLAIMS}
    assert result["evidence"]["formal_kernel_verified"] is False
    assert result["independence"]["audit_recomputed_dependencies"] is True
    assert "finite-sum" in result["evidence"]["scope"]


@pytest.mark.parametrize("mutation", ["beta", "statement", "extra", "variant"])
def test_modified_definitions_and_arbitrary_claims_cannot_receive_proved(mutation):
    raw = algebra.recipe("derive_a")
    if mutation == "beta":
        raw["definitions"]["beta"] = "0.58496"
    elif mutation == "statement":
        raw["claims"]["PILOT-A0"] = "Collatz is true"
    elif mutation == "extra":
        raw["execute"] = "print('escape')"
    else:
        raw["variant"] = "discover"
    with pytest.raises(ValueError):
        algebra.execute_recipe(raw, {})


def test_audit_rejects_forged_certificate_and_missing_lane():
    dependencies = outputs()
    dependencies["derive_b"]["evidence"]["coefficient_certificate"][0]["denominator"] = 99
    with pytest.raises(ValueError, match="recomputed"):
        algebra.execute_recipe(algebra.recipe("audit"), dependencies)
    with pytest.raises(ValueError, match="Missing"):
        algebra.execute_recipe(algebra.recipe("audit"), {})


def test_cannot_duplicate_a_to_claim_b_independence():
    deps = outputs()
    deps["derive_b"] = copy.deepcopy(deps["derive_a"])
    with pytest.raises(ValueError, match="exactly one"):
        algebra.execute_recipe(algebra.recipe("audit"), deps)


def test_private_independent_lane_rejects_dependency_visibility():
    with pytest.raises(ValueError, match="must not receive"):
        algebra.execute_recipe(algebra.recipe("derive_b"), outputs())


def test_exact_boundary_cases_prevent_uniform_overclaim():
    result = algebra.execute_recipe(algebra.recipe("boundary"), {})
    checks = result["public_findings"]["boundary_checks"]
    assert checks["half_integer_squared_moduli"] == ["1", "0", "1/9", "1/4", "9/25"]
    assert checks["state_multipliers"] == ["4/9", "8/9", "16/9", "32/9", "64/9"]
    assert "no uniform contraction" in checks["near_integer"]
    assert "b=0" in checks["b_zero"]


def test_outputs_and_recipes_are_fresh_and_deterministic():
    first = algebra.execute_recipe(algebra.recipe("audit"), outputs())
    assert first == algebra.execute_recipe(algebra.recipe("audit"), outputs())
    first["public_findings"]["definitions"]["beta"] = "corruption"
    assert algebra.recipe("derive_a")["definitions"]["beta"] == "log(3)/log(2)-1"
