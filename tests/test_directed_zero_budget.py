"""Zero model budgets are valid only for entirely native plans."""
import json
from pathlib import Path

import pytest

from lab.directed import preflight_directed_task, run_directed_task
from lab.directed_budget import BudgetExhausted, DirectedBudget
from lab.directed_contract import DirectedTaskContract
from lab.directed_gate import sha
from test_directed import contract

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIELDS = ["max_total_llm_calls", "max_calls_per_lane", "max_total_tokens", "max_tokens_per_lane", "max_total_cost_usd"]


@pytest.mark.parametrize("field", FIELDS)
def test_zero_ceiling_is_rejected_for_any_model_lane(field):
    raw = contract()
    raw["budget"][field] = 0
    with pytest.raises(ValueError, match="positive.*native-only"):
        DirectedTaskContract.model_validate(raw)


@pytest.mark.parametrize("example", ["directed_pilot", "directed_pilot_zero"])
def test_native_effective_model_ceilings_zero_even_for_legacy_nominal_budget(example, monkeypatch, tmp_path):
    import lab.directed_gate as gate
    folder = EXAMPLES / example
    raw = json.loads((folder / "TASK_CONTRACT.json").read_text())
    monkeypatch.setattr(gate, "parent_commit", lambda _: raw["parent_state_commit"])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = preflight_directed_task(folder / "TASK_CONTRACT.json", input_root=folder,
                                     parent_repo=tmp_path, root=tmp_path / "state")
    assert report["gate_status"] == "FEASIBLE_WITH_LIMITATIONS", report
    for field in ["estimated_call_ceiling", "token_ceiling", "cost_ceiling",
                  "effective_model_call_ceiling", "effective_model_token_ceiling", "effective_model_cost_ceiling"]:
        assert report[field] == 0


def test_zero_native_pilot_runs_without_model_or_credentials(monkeypatch, tmp_path):
    import lab.directed_gate as gate
    folder = EXAMPLES / "directed_pilot_zero"
    raw = json.loads((folder / "TASK_CONTRACT.json").read_text())
    monkeypatch.setattr(gate, "parent_commit", lambda _: raw["parent_state_commit"])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class NoModel:
        def call(self, *args, **kwargs):
            raise AssertionError("Native task attempted forbidden model call")

    result = run_directed_task(folder / "TASK_CONTRACT.json", input_root=folder,
                               parent_repo=tmp_path, root=tmp_path / "state", provider=NoModel())
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    summary = json.loads((Path(result.artifact_root) / "package/RUN_SUMMARY.json").read_text())
    for field in ["calls", "reserved_tokens", "reserved_cost_usd", "provider_cost_usd"]:
        assert summary["usage"][field] == 0
    c = DirectedTaskContract.model_validate(raw)
    with pytest.raises(BudgetExhausted):
        DirectedBudget(c.budget).reserve(c.agent_plan.lanes[0], "Cannot reserve model work")


def test_new_zero_pilot_keeps_old_sealed_contract_and_same_recipe_hashes():
    old = json.loads((EXAMPLES / "directed_pilot/TASK_CONTRACT.json").read_text())
    folder = EXAMPLES / "directed_pilot_zero"
    new = json.loads((folder / "TASK_CONTRACT.json").read_text())
    assert old["task_id"] == "small-b-native-pilot-v1"
    assert old["budget"]["max_total_llm_calls"] == 1
    assert new["task_id"] == "small-b-native-pilot-zero-v2"
    assert new["project_id"] != old["project_id"]
    assert all(new["budget"][field] == 0 for field in FIELDS)
    assert new["inputs"] == old["inputs"]
    for item in new["inputs"]:
        assert sha((folder / item["path"]).read_bytes()) == item["sha256"]
