from __future__ import annotations

from lab.research_state import ResearchState
from lab.status_guard import choose_status
from lab.tools import ToolResult


def test_llm_only_counterexample_stays_active_refutation_candidate(tmp_path):
    decision = choose_status(
        "FAIL",
        tool_result=None,
        verifier={"verdict": "FAIL", "counterexample": "n=7 allegedly violates the claim"},
        critic={"verdict": "KEEP", "counterexample": ""},
    )
    assert decision.granted == "REFUTATION_CANDIDATE"
    assert decision.metadata["llm_counterexample"]
    assert decision.metadata["deterministic_counterexample"] is False

    state = ResearchState(tmp_path / "state")
    candidate = state.add_item("conjecture", "candidate", "forall n, P(n)")
    state.update_item(
        candidate.id,
        status=decision.granted,
        metadata={"status_guard": decision.metadata},
    )

    assert state.get(candidate.id).status == "REFUTATION_CANDIDATE"
    assert state.list_items(kind="counterexample") == []
    context = state.research_context()
    assert "ACTIVE CANDIDATES:" in context
    assert "[REFUTATION_CANDIDATE]" in context
    assert "REJECTED IDEAS - DO NOT REOPEN:" not in context


def test_deterministic_tropical_counterexample_forces_fail_and_can_be_recorded(tmp_path):
    tool = ToolResult(
        False,
        "tropical_grid",
        metadata={"status": "COUNTEREXAMPLE", "weights": {"1-2": 0}},
    )
    decision = choose_status(
        "OPEN",
        tool_result=tool,
        verifier={"verdict": "INCONCLUSIVE", "counterexample": ""},
        critic={"verdict": "KEEP", "counterexample": ""},
    )
    assert decision.granted == "FAIL"
    assert decision.metadata["deterministic_counterexample"] is True

    state = ResearchState(tmp_path / "state")
    candidate = state.add_item("conjecture", "candidate", "forall n, P(n)")
    counterexample = state.add_counterexample(
        candidate.id,
        "Deterministic tropical grid counterexample",
        payload=tool.metadata,
    )
    assert state.get(candidate.id).status == "FAIL"
    assert counterexample.metadata["target_id"] == candidate.id


def test_verified_script_counterexample_is_deterministic():
    decision = choose_status(
        "OPEN",
        tool_result=ToolResult(
            True,
            "script",
            metadata={"counterexample_verified": True},
        ),
        verifier={"verdict": "INCONCLUSIVE", "counterexample": ""},
        critic={"verdict": "KEEP", "counterexample": ""},
    )
    assert decision.granted == "FAIL"
    assert decision.metadata["deterministic_counterexample_type"] == "VERIFIED_TOOL"
