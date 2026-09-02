from __future__ import annotations

from lab.status_guard import choose_status
from lab.tools import Z3Tool


def _guard(result):
    return choose_status(
        "COMPUTATION_PASS",
        tool_result=result,
        verifier={"verdict": "INCONCLUSIVE", "counterexample": ""},
        critic={"verdict": "KEEP", "counterexample": ""},
    )


def test_empty_z3_is_explicitly_inconclusive_and_cannot_upgrade():
    result = Z3Tool().check("")
    assert not result.ok
    assert result.error == "no assertions"
    assert result.metadata["result"] == "inconclusive"
    assert result.metadata["assertion_count"] == 0
    assert _guard(result).granted == "OPEN"


def test_assertion_free_smtlib_is_inconclusive():
    result = Z3Tool().check("(set-logic QF_LIA)")
    assert not result.ok
    assert result.error == "no assertions"
    assert result.metadata["result"] == "inconclusive"
    assert result.metadata["assertion_count"] == 0


def test_nonempty_unsat_query_is_computation_evidence():
    result = Z3Tool().check("(assert false)")
    assert result.ok
    assert result.metadata["result"] == "unsat"
    assert result.metadata["assertion_count"] == 1
    assert _guard(result).granted == "COMPUTATION_PASS"
