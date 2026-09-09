"""Real-pilot failure shapes reproduced locally; never calls a model."""
import json
from pathlib import Path

import pytest

from lab.directed import run_directed_task, verify_directed_task
from test_directed import COMMIT, FakeProvider, contract


def run(tmp_path, monkeypatch, provider):
    import lab.directed_gate as gate
    monkeypatch.setattr(gate, "parent_commit", lambda _: COMMIT)
    raw = contract()
    raw["budget"]["max_retries_per_call"] = 0
    raw["agent_plan"]["lanes"] = raw["agent_plan"]["lanes"][:3]
    raw["agent_plan"]["lanes"][2]["depends_on"] = ["0", "1"]
    raw["agent_plan"]["lanes"][2]["can_read_other_lane_outputs"] = True
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return run_directed_task(path, provider=provider, root=tmp_path / "state")


def test_partial_timeout_blocks_audit_and_keeps_early_identity(tmp_path, monkeypatch):
    class Pilot(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            self.calls.append(lane.lane_id)
            callback("metadata", {"generation_id": "gen-" + lane.lane_id})
            callback("reasoning", "Synthetic reasoning only")
            if lane.lane_id == "1":
                raise TimeoutError("Synthetic timeout")
            return {"content": "", "complete": False, "finish_reason": "length",
                    "usage": {"completion_tokens": 128, "reasoning_tokens": 128,
                              "cost_usd": 0.0001}}

    provider = Pilot()
    result = run(tmp_path, monkeypatch, provider)
    assert result.status == "PARTIAL"
    folder = Path(result.artifact_root)
    summary = json.loads((folder / "package/RUN_SUMMARY.json").read_text())
    assert summary["lanes"] == {"0": "PARTIAL", "1": "TIMEOUT", "2": "BLOCKED_DEPENDENCY"}
    assert sorted(provider.calls) == ["0", "1"]
    assert summary["usage"]["provider_cost_complete"] is False
    assert summary["usage"]["provider_cost_usd"] == 0.0001
    partial = json.loads((folder / "package/LANES/1/PARTIAL.json").read_text())
    attempt = partial["provider_attempts"][0]
    assert attempt["metadata"]["generation_id"] == "gen-1"
    assert attempt["client_call_id"]
    assert attempt["status"] == "INTERRUPTED"
    lane = json.loads((folder / "lanes/1/RESULT.json").read_text())
    assert lane["provider_attempts"] == partial["provider_attempts"]
    assert verify_directed_task("test-project", result.run_id, root=tmp_path / "state")["ok"]


@pytest.mark.parametrize("content", ["", "   ", '{"findings":""}'])
def test_empty_final_answer_never_unblocks_auditor(tmp_path, monkeypatch, content):
    class Empty(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            self.calls.append(lane.lane_id)
            return {"content": content, "complete": True, "finish_reason": "stop",
                    "usage": {"cost_usd": 0}}
    provider = Empty()
    result = run(tmp_path, monkeypatch, provider)
    summary = json.loads((Path(result.artifact_root) / "package/RUN_SUMMARY.json").read_text())
    assert summary["lanes"]["2"] == "BLOCKED_DEPENDENCY"
    assert sorted(provider.calls) == ["0", "1"]
    assert summary["usage"]["provider_cost_complete"] is True
