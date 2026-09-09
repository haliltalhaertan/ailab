"""Output allowances are validated without promising provider enforcement."""
import json

import pytest

from lab.directed_contract import Lane
from lab.directed_gate import CapabilityAndIntegrityGate
from test_directed import contract


def model_lane(**policy):
    return Lane(lane_id="producer", role="Theorist", model="fake/model",
                input_price_per_million=1, output_price_per_million=1,
                max_completion_tokens=5000, **policy)


@pytest.mark.parametrize("policy", [
    {"max_reasoning_tokens": 4000, "min_final_answer_tokens": 1001},
    {"max_reasoning_tokens": 5001},
    {"min_final_answer_tokens": 1000, "reasoning_effort": "medium"},
    {"min_final_answer_tokens": 1000},
    {"max_reasoning_tokens": 3000, "reasoning_effort": "none"},
    {"max_reasoning_tokens": 0},
    {"max_reasoning_tokens": True},
    {"min_final_answer_tokens": -1},
    {"min_final_answer_tokens": True},
])
def test_invalid_output_policy_is_rejected_before_dispatch(policy):
    with pytest.raises(ValueError):
        model_lane(**policy)


@pytest.mark.parametrize("policy", [
    {"max_reasoning_tokens": 3000, "min_final_answer_tokens": 2000},
    {"reasoning_effort": "none", "min_final_answer_tokens": 5000},
    {"reasoning_effort": "high"},
    {},
])
def test_valid_explicit_and_legacy_policies(policy):
    assert model_lane(**policy).max_completion_tokens == 5000


def test_native_lane_cannot_claim_model_answer_allowance():
    with pytest.raises(ValueError, match="only to model"):
        Lane(lane_id="native", role="Auditor", execution="integrity",
             reasoning_effort="none", min_final_answer_tokens=100)


@pytest.mark.parametrize("policy,classification,exhaustion", [
    ({"max_reasoning_tokens": 80, "min_final_answer_tokens": 48},
     "REQUESTED_NUMERIC_REASONING_CAP", False),
    ({"reasoning_effort": "none", "min_final_answer_tokens": 128},
     "REQUESTED_REASONING_DISABLED", False),
    ({"reasoning_effort": "high"}, "EFFORT_ONLY_OR_PROVIDER_DEFAULT", True),
])
def test_preflight_reports_nominal_policy_without_calling_provider(tmp_path, monkeypatch, policy,
                                                                  classification, exhaustion):
    import lab.directed_gate as gate
    raw = contract()
    raw["agent_plan"]["lanes"] = [raw["agent_plan"]["lanes"][0] | policy]
    path = tmp_path / "TASK_CONTRACT.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(gate, "parent_commit", lambda _: raw["parent_state_commit"])

    class NoCalls:
        def call(self, *args, **kwargs):
            raise AssertionError("Preflight must not call a model")

    report = CapabilityAndIntegrityGate().check(path, provider=NoCalls())
    assert report["gate_status"] == "FEASIBLE_WITH_LIMITATIONS", report
    assert report["llm_calls_made"] == 0
    item = report["output_budget_policies"][0]
    assert item["policy"] == classification
    assert item["final_answer_guaranteed"] is False
    assert item["provider_support_verified"] is False
    assert item["automatic_finalization_call"] is False
    assert any("Reasoning exhaustion risk" in x for x in report["limitations"]) is exhaustion
    assert any("nominal allowance" in x for x in report["limitations"])
