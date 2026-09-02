from pathlib import Path

import pytest

from lab.research_state import ResearchState


def test_counterexample_closes_claim(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    state.freeze_problem("P")
    claim = state.add_item("conjecture", "candidate", "forall n, f(n) <= n^2")
    counterexample = state.add_counterexample(
        claim.id,
        "n=7 violates the claim",
        payload={"n": 7},
    )
    assert counterexample.status == "KNOWN"
    assert state.get(claim.id).status == "FAIL"


def test_llm_claim_cannot_be_marked_proven_with_flag_only(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    claim = state.add_item("lemma", "candidate", "A implies B")
    with pytest.raises(ValueError):
        state.update_item(claim.id, status="PROVEN")
    # A literal formal_verified flag is no longer a proof. PROVEN requires the
    # same-item bound Lean source, SHA, statement binding, axiom audit and HMAC
    # seal produced by the formal verification path.
    with pytest.raises(ValueError):
        state.update_item(
            claim.id,
            status="PROVEN",
            metadata={"formal_verified": True, "verifier": "lean"},
        )
    assert state.get(claim.id).status == "OPEN"


def test_problem_freeze_prevents_accidental_problem_switch(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    state.freeze_problem("P")
    state.freeze_problem("P")
    with pytest.raises(ValueError):
        state.freeze_problem("Q")


def test_checkpoint_is_written(tmp_path: Path):
    state = ResearchState(tmp_path / "state")
    state.freeze_problem("P")
    state.add_item("conjecture", "candidate", "C")
    path = state.checkpoint("cp1")
    assert path.exists()
