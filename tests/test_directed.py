from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time

import pytest

from lab.directed import (
    run_directed_task,
    resume_directed_task,
    preflight_directed_task,
    verify_directed_task,
    validate_contract,
)
from lab.directed_budget import DirectedBudget, BudgetExhausted
from lab.directed_contract import DirectedTaskContract
from lab.directed_engine import ClaimStatusValidator
from lab.directed_gate import redact
from lab.integrity import ProjectRunLock


COMMIT = "a" * 40


def contract():
    return {
        "schema_version": "1.0",
        "task_id": "test-task",
        "project_id": "test-project",
        "parent_task_id": "principal",
        "parent_state_commit": COMMIT,
        "created_at": "2026-09-07T00:00:00Z",
        "created_by": "principal_researcher",
        "mode": "directed_task",
        "objective": "Check the declared identity only",
        "task_type": "analytic_derivation",
        "primary_claim": {
            "claim_id": "claim",
            "statement": "Given identity",
            "status_at_start": "OPEN",
            "quantifiers": "real x",
            "definitions": ["Declared definition"],
        },
        "scope": {"included": ["Identity review"], "excluded": ["New research"]},
        "inputs": [],
        "input_manifests": [],
        "allowed_tools": [],
        "forbidden_tools": ["host_generated_code_execution"],
        "allowed_methods": ["exact_algebra"],
        "forbidden_methods": ["silent_scope_expansion"],
        "agent_plan": {
            "strategy": "parallel_independent",
            "max_parallel_workers": 4,
            "lanes": [
                {
                    "lane_id": str(i),
                    "role": "Theorist",
                    "independence_group": str(i),
                    "can_read_other_lane_outputs": False,
                    "model": "fake/model",
                    "input_price_per_million": 1,
                    "output_price_per_million": 1,
                    "max_completion_tokens": 128,
                }
                for i in range(4)
            ],
        },
        "budget": {
            "max_total_llm_calls": 10,
            "max_calls_per_lane": 3,
            "max_total_tokens": 200000,
            "max_tokens_per_lane": 60000,
            "max_total_cost_usd": 10,
            "max_wall_seconds": 60,
            "max_parallel_workers": 4,
            "max_retries_per_call": 1,
        },
        "stop_rules": ["INPUT_INTEGRITY_FAILURE", "USER_STOP", "BUDGET_EXHAUSTED"],
        "required_outputs": [
            "CAPABILITY_REPORT.json",
            "MASTER_FINDINGS.md",
            "CLAIM_LEDGER.json",
            "RUN_SUMMARY.json",
            "FINAL_SHA256SUMS.txt",
            "VERIFY_OUTPUT.txt",
        ],
        "result_policy": {
            "allow_new_task_creation": False,
            "allow_scope_expansion": False,
            "allow_budget_expansion": False,
            "allow_canonical_repository_write": False,
            "unexpected_result_action": "RECORD_AS_ESCALATION_CANDIDATE",
        },
    }


class FakeProvider:
    identity = "deterministic-test-provider-v1"

    def __init__(self, delay=0.01, fail=None):
        self.delay, self.fail = delay, fail
        self.active = 0
        self.peak = 0
        self.calls = []
        self.intervals = {}
        self.lock = threading.Lock()
        self.prompts = {}

    def call(self, lane, prompt, callback, cancelled, timeout, step_id):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(lane.lane_id)
            self.prompts[lane.lane_id] = prompt
        started = time.monotonic()
        try:
            time.sleep(self.delay)
            if self.fail == lane.lane_id:
                raise RuntimeError("Controlled provider failure")
            callback("reasoning", "PRIVATE REASONING " + lane.lane_id)
            return {
                "content": json.dumps(
                    {
                        "findings": "PUBLIC " + lane.lane_id,
                        "claim_status": "OPEN",
                        "first_missing_step": "Independent proof",
                        "unexpected_findings": ["candidate only"],
                    }
                ),
                "complete": True,
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "cost_usd": 0.0001},
            }
        finally:
            with self.lock:
                self.active -= 1
                self.intervals[lane.lane_id] = (started, time.monotonic())


@pytest.fixture
def setup_task(tmp_path, monkeypatch):
    import lab.directed_gate as gate

    monkeypatch.setattr(gate, "parent_commit", lambda p: COMMIT)

    def setup(raw=None):
        path = tmp_path / "contract.json"
        path.write_text(json.dumps(raw or contract()), encoding="utf-8")
        return path

    return setup, tmp_path / "state"


@pytest.mark.parametrize(
    "modify",
    [
        lambda c: c.pop("task_id"),
        lambda c: c.update(schema_version="9"),
        lambda c: c["agent_plan"]["lanes"].append(deepcopy(c["agent_plan"]["lanes"][0])),
        lambda c: c["agent_plan"]["lanes"][0].update(depends_on=["missing"]),
        lambda c: (
            c["agent_plan"]["lanes"][0].update(depends_on=["1"]),
            c["agent_plan"]["lanes"][1].update(depends_on=["0"]),
        ),
        lambda c: c["budget"].update(max_total_tokens=0),
        lambda c: c.update(allowed_tools=["host_generated_code_execution"]),
        lambda c: c["agent_plan"].update(max_parallel_workers=5),
        lambda c: c["primary_claim"].update(statement=" "),
        lambda c: c["result_policy"].update(allow_scope_expansion=True),
    ],
)
def test_invalid_contract_before_provider(setup_task, modify):
    setup, root = setup_task
    raw = contract()
    modify(raw)
    assert not validate_contract(setup(raw))["ok"]


def test_hash_and_commit_gate_before_calls(setup_task, monkeypatch):
    setup, root = setup_task
    c = contract()
    c["parent_state_commit"] = "b" * 40
    provider = FakeProvider()
    assert preflight_directed_task(setup(c), provider=provider, root=root)["gate_status"] == "INPUT_INTEGRITY_FAILURE"
    c = contract()
    c["inputs"] = [{"path": "input.txt", "sha256": "f" * 64}]
    path = setup(c)
    (path.parent / "input.txt").write_text("wrong")
    assert preflight_directed_task(path, provider=provider, root=root)["gate_status"] == "INPUT_INTEGRITY_FAILURE"
    assert not provider.calls


@pytest.mark.parametrize("workers,maximum_seconds", [(4, 2.5), (2, 3.6)])
def test_real_parallelism_and_semaphore(setup_task, workers, maximum_seconds):
    setup, root = setup_task
    c = contract()
    c["agent_plan"]["max_parallel_workers"] = workers
    provider = FakeProvider(delay=1)
    start = time.monotonic()
    result = run_directed_task(setup(c), provider=provider, root=root)
    elapsed = time.monotonic() - start
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    assert provider.peak == workers
    # Measured provider interval excludes filesystem/CI overhead while still detects serial execution.
    span = max(b for a, b in provider.intervals.values()) - min(a for a, b in provider.intervals.values())
    assert span < maximum_seconds
    assert elapsed >= 1
    assert verify_directed_task(c["project_id"], result.run_id, root=root)["ok"]


def test_dependency_failure_and_independence(setup_task):
    setup, root = setup_task
    c = contract()
    c["agent_plan"]["lanes"][3].update(
        depends_on=["0"], can_read_other_lane_outputs=True, visible_inputs=["selected_artifacts"]
    )
    provider = FakeProvider(fail="0")
    result = run_directed_task(setup(c), provider=provider, root=root)
    data = json.loads((__import__("pathlib").Path(result.artifact_root) / "runtime.json").read_text())
    assert data["lanes"]["3"] == "BLOCKED_DEPENDENCY"
    assert "1" in provider.calls and "2" in provider.calls and "3" not in provider.calls
    assert "PRIVATE REASONING 0" not in provider.prompts["1"]


def test_audit_starts_after_dependencies_without_reasoning(setup_task):
    setup, root = setup_task
    c = contract()
    c["agent_plan"]["lanes"][3].update(
        depends_on=["0", "1", "2"], can_read_other_lane_outputs=True, visible_inputs=["selected_artifacts"]
    )
    provider = FakeProvider()
    run_directed_task(setup(c), provider=provider, root=root)
    assert provider.intervals["3"][0] >= max(provider.intervals[n][1] for n in ["0", "1", "2"])
    assert "PRIVATE REASONING" not in provider.prompts["3"]
    assert "PUBLIC 0" in provider.prompts["3"]


def test_atomic_budget_race():
    c = DirectedTaskContract.model_validate(contract())
    c.budget.max_total_llm_calls = 1
    budget = DirectedBudget(c.budget)

    def reserve(lane):
        try:
            budget.reserve(lane, "prompt")
            return True
        except BudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, c.agent_plan.lanes))
    assert sum(results) == 1


def test_resume_complete_does_not_repay(setup_task):
    setup, root = setup_task
    provider = FakeProvider()
    c = contract()
    result = run_directed_task(setup(c), provider=provider, root=root)
    resumed = resume_directed_task(c["project_id"], result.run_id, provider=provider, root=root)
    assert resumed.status == result.status and len(provider.calls) == 4


def test_project_locks_and_secrets(setup_task):
    setup, root = setup_task
    c = contract()
    path = setup(c)
    with ProjectRunLock(root / c["project_id"]):
        assert preflight_directed_task(path, provider=FakeProvider(), root=root)["gate_status"] == "OUT_OF_SCOPE"
        c["project_id"] = "other"
        assert preflight_directed_task(setup(c), provider=FakeProvider(), root=root)["gate_status"].startswith(
            "FEASIBLE"
        )
    c["inputs"] = [{"path": ".env", "sha256": "a" * 64}]
    assert not validate_contract(setup(c))["ok"]
    assert "sk-" not in redact("Authorization: Bearer sk-pretendcredentialvalue")


def test_claim_status_over_and_under_claim():
    c = DirectedTaskContract.model_validate(contract())
    lane = c.agent_plan.lanes[0]
    state, issues = ClaimStatusValidator().classify(c, lane, {"requested_claim_status": "PROVED"})
    assert state == "OPEN" and "OVERCLAIM" in issues
    c.primary_claim.status_at_start = "PROVED"
    state, issues = ClaimStatusValidator().classify(c, lane, {"requested_claim_status": "NUMERICAL"})
    assert state == "PROVED" and "UNDERCLAIM_REVIEW_REQUIRED" in issues
