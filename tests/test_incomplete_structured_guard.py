from lab.completion_integrity import (
    record_role_completion,
    reset_completion_integrity,
    role_incomplete,
)
from lab.evidence import Evidence
from lab.json_io import IncompleteJSONObject, parse_truncated_object_prefix
from lab.status_guard import choose_status
from lab.step_store import StepStore


def _exact_evidence() -> Evidence:
    return Evidence(
        source="checked",
        source_origin="CHECKED_IN",
        evidence_role="INDEPENDENT_CHECKER",
        resolution_scope="PARTIAL",
        kind="EXACT_PASS",
        ok=True,
        exhaustive=False,
        termination_reason="completed",
        witness=None,
        contract_hash="",
        target_id=None,
        target_hash=None,
        runtime_s=0.0,
        input_sha256="a",
        output_sha256="b",
        tool_sha256="c",
    )


def _complete_reviews():
    verifier = {"verdict": "PASS", "reason": "ok", "counterexample": ""}
    critic = {"verdict": "KEEP", "reason": "ok", "counterexample": ""}
    return verifier, critic


def test_recovered_truncated_manager_fields_resolve_to_safe_decisions():
    recovered = parse_truncated_object_prefix(
        '{"decision":"KEEP","status":"PROVEN","reason":"partial","target_proposal":{"target_id":"T1","status":"CLOSED"},"next_task":"unfinished'
    )

    assert isinstance(recovered, IncompleteJSONObject)
    assert recovered.get("decision") == "REVISE"
    assert recovered.get("status") == "OPEN"
    assert recovered.get("target_proposal") == {}
    assert recovered.get("reason") == "partial"


def test_incomplete_verifier_blocks_even_machine_backed_promotion():
    reset_completion_integrity()
    verifier = IncompleteJSONObject({"verdict": "PASS", "reason": "cut"})
    critic = {"verdict": "KEEP", "reason": "ok", "counterexample": ""}

    decision = choose_status(
        "COMPUTATION_PASS",
        tool_result=None,
        verifier=verifier,
        critic=critic,
        evidence=_exact_evidence(),
    )

    assert decision.granted == "OPEN"
    assert decision.downgraded is True
    assert decision.metadata["verifier_incomplete"] is True
    assert "provider-truncated" in decision.reason
    reset_completion_integrity()


def test_incomplete_critic_blocks_proof_candidate():
    reset_completion_integrity()
    verifier = {"verdict": "PASS", "reason": "ok", "counterexample": ""}
    critic = IncompleteJSONObject({"verdict": "KEEP", "reason": "cut"})

    decision = choose_status(
        "PROOF_CANDIDATE",
        tool_result=None,
        verifier=verifier,
        critic=critic,
    )

    assert decision.granted == "OPEN"
    assert decision.metadata["critic_incomplete"] is True
    reset_completion_integrity()


def test_valid_json_with_provider_length_is_still_non_promotable():
    reset_completion_integrity()
    verifier, critic = _complete_reviews()
    record_role_completion("ResearchManager", truncated=True)

    decision = choose_status(
        "COMPUTATION_PASS",
        tool_result=None,
        verifier=verifier,
        critic=critic,
        evidence=_exact_evidence(),
    )

    assert decision.granted == "OPEN"
    assert decision.metadata["manager_incomplete"] is True
    reset_completion_integrity()


def test_cached_valid_truncation_restores_role_completion_state(tmp_path):
    reset_completion_integrity()
    store = StepStore(tmp_path / "state")
    store.put_step(
        "iter:3:manager",
        {
            "status": "COMPLETE",
            "fingerprint": "f",
            "content": '{"decision":"KEEP","status":"COMPUTATION_PASS"}',
            "model": "fake/model",
            "truncated": True,
        },
    )

    payload = store.get_step("iter:3:manager")

    assert payload is not None
    assert role_incomplete("ResearchManager") is True
    reset_completion_integrity()
