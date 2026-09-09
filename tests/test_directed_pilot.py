"""Full native directed pilot; no LLM/network service is called."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading

from lab.directed import preflight_directed_task, run_directed_task
from lab.directed_package import verify_package

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "directed_pilot"


class ForbiddenProvider:
    identity = "no-model-calls-permitted"

    def call(self, *args, **kwargs):
        raise AssertionError("Native pilot must never invoke a model")


def test_full_native_pilot_three_parallel_then_audit(tmp_path, monkeypatch):
    import lab.directed_algebra as algebra
    import lab.directed_gate as gate

    fixture = tmp_path / "inputs"
    shutil.copytree(FIXTURE, fixture)
    raw = json.loads((fixture / "TASK_CONTRACT.json").read_text())
    monkeypatch.setattr(gate, "parent_commit", lambda _: raw["parent_state_commit"])
    original = algebra.execute_recipe
    arrived = set()
    lock = threading.Lock()
    barrier = threading.Barrier(3, timeout=15)

    def instrumented(recipe, dependencies):
        # The actual native algebra stays unchanged. Hold the first three lanes
        # at a barrier so sequential scheduling cannot silently pass the test.
        variant = recipe["variant"]
        with lock:
            first = variant not in arrived
            arrived.add(variant)
        if variant != "audit" and first:
            assert dependencies == {}
            barrier.wait()
        if variant == "audit":
            assert set(dependencies) == {"derive_a", "derive_b", "boundary"}
        return original(recipe, dependencies)

    monkeypatch.setattr(algebra, "execute_recipe", instrumented)
    provider = ForbiddenProvider()
    contract = fixture / "TASK_CONTRACT.json"
    preflight = preflight_directed_task(
        contract, input_root=fixture, parent_repo=tmp_path, root=tmp_path / "state", provider=provider
    )
    assert preflight["gate_status"] == "FEASIBLE_WITH_LIMITATIONS", preflight
    result = run_directed_task(
        contract, input_root=fixture, parent_repo=tmp_path, root=tmp_path / "state", provider=provider
    )
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    folder = Path(result.artifact_root)
    final = json.loads((folder / "lanes/audit/RESULT.json").read_text())
    statuses = {claim["claim_id"]: claim["status"] for claim in final["claim_results"]}
    assert statuses == {
        "PILOT-A0": "PROVED",
        "PILOT-A1": "PROVED",
        "PILOT-GENERAL-MODULUS": "PROVED",
        "PILOT-EQUALITY": "PROVED",
        "PILOT-XUB": "OPEN",
    }
    summary = json.loads((folder / "package/RUN_SUMMARY.json").read_text())
    assert set(summary["lanes"].values()) == {"COMPLETED"}
    assert summary["usage"]["calls"] == 0
    assert summary["new_tasks_started"] == 0
    assert all(
        claim["status"] == "OPEN"
        for claim in json.loads(Path(result.claim_ledger_path).read_text())["claims"]
        if claim["claim_id"].startswith("PILOT-XUB")
    )
    ledger = json.loads(Path(result.claim_ledger_path).read_text())
    assert ledger["schema_version"] == "1.1" and ledger["authoritative"]
    assert {x["claim_id"]: x["status"] for x in ledger["claims"]} == statuses
    assert len(ledger["claims"]) == 5
    assert all(x["supporting_evidence"] for x in ledger["claims"])
    assert not ledger["mathematical_truth_verified_by_package"]
    verified = verify_package(folder / "package")
    assert verified["ok"], verified


def test_pilot_inputs_are_explicit_and_hash_bound():
    from lab.directed_contract import DirectedTaskContract
    from lab.directed_gate import sha

    contract = DirectedTaskContract.model_validate_json((FIXTURE / "TASK_CONTRACT.json").read_bytes())
    assert contract.parent_state_commit == "db2d862fcdbd06c3dfac6bdac191bf9b7017717a"
    for item in contract.inputs:
        assert sha((FIXTURE / item.path).read_bytes()) == item.sha256
    assert [lane.execution for lane in contract.agent_plan.lanes] == ["symbolic_algebra"] * 4
