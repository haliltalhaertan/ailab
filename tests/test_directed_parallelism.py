"""Configured concurrency beyond four, using a local barrier provider."""
from copy import deepcopy
import json
import threading

import pytest
from pydantic import ValidationError

from lab.directed import run_directed_task, preflight_directed_task
from lab.directed_contract import DirectedTaskContract
from test_directed import COMMIT, FakeProvider, contract


def test_six_independent_calls_can_run_together(tmp_path, monkeypatch):
    import lab.directed_gate as gate
    monkeypatch.setattr(gate, "parent_commit", lambda _: COMMIT)
    raw = contract()
    template = raw["agent_plan"]["lanes"][0]
    raw["agent_plan"]["lanes"] = [dict(deepcopy(template), lane_id=str(i)) for i in range(6)]
    raw["agent_plan"]["max_parallel_workers"] = 6
    raw["budget"]["max_parallel_workers"] = 6
    raw["budget"]["max_retries_per_call"] = 0
    barrier = threading.Barrier(6, timeout=15)

    class Provider(FakeProvider):
        def call(self, *args, **kwargs):
            barrier.wait()
            return super().call(*args, **kwargs)

    provider = Provider()
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert preflight_directed_task(path, provider=provider)["maximum_concurrency"] == 6
    result = run_directed_task(path, provider=provider, root=tmp_path / "state")
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    assert len(provider.calls) == 6


@pytest.mark.parametrize("workers", [15, 32, 100])
def test_no_fixed_worker_ceiling(workers):
    raw = contract()
    raw["agent_plan"]["max_parallel_workers"] = workers
    raw["budget"]["max_parallel_workers"] = workers
    assert DirectedTaskContract.model_validate(raw).agent_plan.max_parallel_workers == workers


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_worker_count_still_requires_positive_integer(workers):
    raw = contract()
    raw["budget"]["max_parallel_workers"] = workers
    with pytest.raises(ValidationError):
        DirectedTaskContract.model_validate(raw)


def test_plan_cannot_exceed_declared_budget():
    raw = contract()
    raw["agent_plan"]["max_parallel_workers"] = 16
    raw["budget"]["max_parallel_workers"] = 15
    with pytest.raises(ValidationError):
        DirectedTaskContract.model_validate(raw)
