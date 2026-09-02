from __future__ import annotations

from lab.status_guard import choose_status
from lab.tools import TropicalGridTool


def _k3_with_redundant_walk_term() -> dict:
    return {
        "n": 3,
        "gates": [
            {"id": "e12", "op": "edge", "u": 1, "v": 2},
            {"id": "e13", "op": "edge", "u": 1, "v": 3},
            {"id": "e23", "op": "edge", "u": 2, "v": 3},
            {"id": "via2", "op": "add", "args": ["e12", "e23"]},
            {
                "id": "out",
                "op": "min",
                "args": ["e13", "via2", "redundant_walk"],
            },
            {
                "id": "redundant_walk",
                "op": "add",
                "args": ["e12", "e12", "e13"],
            },
        ],
        "output": "out",
    }


def test_k3_gate_count_means_internal_gates():
    circuit = {
        "n": 3,
        "gates": [
            {"id": "e12", "op": "edge", "u": 1, "v": 2},
            {"id": "e13", "op": "edge", "u": 1, "v": 3},
            {"id": "e23", "op": "edge", "u": 2, "v": 3},
            {"id": "via2", "op": "add", "args": ["e12", "e23"]},
            {"id": "out", "op": "min", "args": ["e13", "via2"]},
        ],
        "output": "out",
    }
    result = TropicalGridTool().check(circuit, [0, 1, 2])
    assert result.ok
    assert result.metadata["status"] == "GRID_PASS"
    assert result.metadata["gate_count"] == 2
    assert result.metadata["edge_gate_count"] == 3
    assert "monomial" in result.metadata["warning"].lower()


def test_redundant_non_simple_walk_can_grid_pass_without_structural_claim():
    # Put the referenced gate before the output because circuit evaluation is
    # intentionally topological rather than name-resolving.
    circuit = _k3_with_redundant_walk_term()
    circuit["gates"][4], circuit["gates"][5] = circuit["gates"][5], circuit["gates"][4]
    result = TropicalGridTool().check(circuit, [0, 1, 2])
    assert result.ok
    assert result.metadata["status"] == "GRID_PASS"
    assert result.metadata["gate_count"] == 3
    assert result.metadata["edge_gate_count"] == 3
    assert result.metadata["functional_equality_only"] is True
    assert "monomial-level" in result.metadata["warning"]

    decision = choose_status(
        "COMPUTATION_PASS",
        tool_result=result,
        verifier={"verdict": "INCONCLUSIVE", "counterexample": ""},
        critic={"verdict": "KEEP", "counterexample": ""},
    )
    assert decision.granted == "COMPUTATION_PASS"
    assert decision.metadata["formal_verified"] is False
