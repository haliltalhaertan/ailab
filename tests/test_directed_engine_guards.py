"""Adversarial runtime tests; all providers are local synthetic fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.directed import resume_directed_task, run_directed_task
from lab.directed_engine import write
from lab.directed_gate import sha
from test_directed import COMMIT, FakeProvider, contract


@pytest.fixture
def task(tmp_path, monkeypatch):
    import lab.directed_gate as gate
    monkeypatch.setattr(gate, "parent_commit", lambda _: COMMIT)
    raw = contract()
    raw["agent_plan"]["strategy"] = "sequential"
    raw["budget"]["max_retries_per_call"] = 0
    path = tmp_path / "contract.json"

    def save():
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    return raw, save, tmp_path / "state"


def test_partial_resume_requires_existing_sealed_cumulative_budget(task):
    raw, save, root = task
    provider = FakeProvider(fail="0")
    result = run_directed_task(save(), provider=provider, root=root)
    assert result.status == "PARTIAL"
    budget = Path(result.artifact_root) / "private/budget.json"
    assert budget.is_file()
    budget.unlink()
    before = len(provider.calls)
    with pytest.raises(ValueError, match="(?i)budget"):
        resume_directed_task(raw["project_id"], result.run_id, provider=provider, root=root)
    assert len(provider.calls) == before


def test_authorization_redaction_preserves_nested_json_structure(tmp_path):
    output = tmp_path / "response.json"
    raw = {"content": "Authorization: Bearer sk-syntheticcredentialvalue\nSafe continuation",
           "usage": {"completion_tokens": 31},
           "details": [{"text": "Authorization=pretendcredentialvalue", "other": "preserved"}],
           "complete": True}
    write(output, raw)
    encoded = output.read_text()
    parsed = json.loads(encoded)
    assert parsed["usage"] == {"completion_tokens": 31}
    assert parsed["details"][0]["other"] == "preserved"
    assert parsed["complete"] is True
    assert "Safe continuation" in parsed["content"]
    assert "syntheticcredentialvalue" not in encoded
    assert "pretendcredentialvalue" not in encoded
    assert "[REDACTED]" in encoded


@pytest.mark.parametrize("target", ["snapshot", "contract"])
def test_mid_callback_integrity_tampering_invalidates_all_claims(task, target):
    raw, save, root = task
    raw["agent_plan"]["lanes"] = raw["agent_plan"]["lanes"][:1]
    path = save()
    source = path.parent / "definitions.txt"
    source.write_text("fixed definitions", encoding="utf-8")
    raw["inputs"] = [{"path": source.name, "sha256": sha(source.read_bytes())}]
    raw["agent_plan"]["lanes"][0]["input_paths"] = [source.name]

    class TamperingProvider(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            def mutate_during_callback(channel, delta):
                folder = next((root / raw["project_id"] / "directed").iterdir())
                artifact = folder / ("inputs/definitions.txt" if target == "snapshot" else "TASK_CONTRACT.json")
                if target == "snapshot":
                    artifact.write_text("silently changed definition", encoding="utf-8")
                else:
                    changed = json.loads(artifact.read_text())
                    changed["objective"] = "silently changed objective"
                    artifact.write_text(json.dumps(changed), encoding="utf-8")
                callback(channel, delta)
            return super().call(lane, prompt, mutate_during_callback, cancelled, timeout, step_id)

    result = run_directed_task(save(), provider=TamperingProvider(), root=root)
    assert result.status == "INPUT_INTEGRITY_FAILURE"
    ledger = json.loads(Path(result.claim_ledger_path).read_text())
    assert ledger["claims"]
    assert {claim["status"] for claim in ledger["claims"]} == {"INPUT_INTEGRITY_FAILURE"}


@pytest.mark.parametrize("excess", ["tokens", "cost"])
def test_provider_reservation_overrun_stops_remaining_lanes(task, excess):
    _, save, root = task

    class OverrunProvider(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            response = super().call(lane, prompt, callback, cancelled, timeout, step_id)
            response["usage"]["completion_tokens" if excess == "tokens" else "cost_usd"] = 10**9
            return response

    provider = OverrunProvider()
    result = run_directed_task(save(), provider=provider, root=root)
    assert result.status == "BUDGET_EXHAUSTED"
    assert provider.calls == ["0"]
    summary = json.loads((Path(result.artifact_root) / "package/RUN_SUMMARY.json").read_text())
    assert summary["usage"]["calls"] == 1
    assert summary["lanes"]["0"] == "BUDGET_EXHAUSTED"
    assert all(value == "STOPPED" for lane, value in summary["lanes"].items() if lane != "0")


class SyntheticHTTPError(RuntimeError):
    def __init__(self, status_code):
        super().__init__(f"Synthetic provider HTTP {status_code}")
        self.status_code = status_code


def test_auth_401_has_no_retry_and_stops_other_lanes(task):
    raw, save, root = task
    raw["agent_plan"]["max_parallel_workers"] = 1
    raw["budget"]["max_retries_per_call"] = 2

    class Unauthorized(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            self.calls.append(lane.lane_id)
            raise SyntheticHTTPError(401)

    provider = Unauthorized()
    result = run_directed_task(save(), provider=provider, root=root)
    assert result.status == "PAUSED_ERROR"
    assert provider.calls == ["0"]
    summary = json.loads((Path(result.artifact_root) / "package/RUN_SUMMARY.json").read_text())
    assert summary["usage"]["calls"] == 1
    assert all(status == "STOPPED" for lane, status in summary["lanes"].items() if lane != "0")


def test_transient_429_retries_once_within_call_cap_then_succeeds(task):
    raw, save, root = task
    raw["agent_plan"]["lanes"] = raw["agent_plan"]["lanes"][:1]
    raw["agent_plan"]["max_parallel_workers"] = 1
    raw["budget"]["max_retries_per_call"] = 1
    raw["budget"]["max_total_llm_calls"] = 2
    raw["budget"]["max_calls_per_lane"] = 2

    class Transient(FakeProvider):
        attempts = 0

        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            self.attempts += 1
            if self.attempts == 1:
                self.calls.append(lane.lane_id)
                raise SyntheticHTTPError(429)
            return super().call(lane, prompt, callback, cancelled, timeout, step_id)

    provider = Transient()
    result = run_directed_task(save(), provider=provider, root=root)
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    assert provider.calls == ["0", "0"]
    summary = json.loads((Path(result.artifact_root) / "package/RUN_SUMMARY.json").read_text())
    assert summary["usage"]["calls"] == 2
    assert summary["usage"]["lanes"]["0"]["calls"] == 2


def test_streaming_user_stop_preserves_partial_and_prevents_successors(task):
    raw, save, root = task
    raw["agent_plan"]["max_parallel_workers"] = 1
    raw["budget"]["max_retries_per_call"] = 2

    class StreamingStop(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            self.calls.append(lane.lane_id)
            callback("reasoning", "Synthetic visible explanation before cancellation.")
            (root / raw["project_id"] / "stop.flag").write_text("stop", encoding="utf-8")
            callback("content", "Last visible partial output.")
            raise AssertionError("Cancellation callback must interrupt the provider")

    provider = StreamingStop()
    result = run_directed_task(save(), provider=provider, root=root)
    assert result.status == "STOPPED"
    assert provider.calls == ["0"]
    folder = Path(result.artifact_root)
    partial = json.loads((folder / "lanes/0/PARTIAL.json").read_text())
    assert partial["reasoning"] == "Synthetic visible explanation before cancellation."
    assert partial["content"] == "Last visible partial output."
    assert partial["status"] == "PARTIAL_PROVIDER_VISIBLE"
    assert json.loads((folder / "package/LANES/0/PARTIAL.json").read_text()) == partial
    summary = json.loads((folder / "package/RUN_SUMMARY.json").read_text())
    assert set(summary["lanes"].values()) == {"STOPPED"}
    assert summary["usage"]["calls"] == 1
