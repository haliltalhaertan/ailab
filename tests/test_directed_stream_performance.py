import json
from pathlib import Path

import pytest

from lab.directed import run_directed_task
from lab.directed_engine import redact_values
from test_directed import COMMIT, FakeProvider, contract


def test_nested_redaction_reads_environment_once(monkeypatch):
    import lab.directed_gate as gate
    calls = []
    monkeypatch.setattr(gate, "secrets", lambda: calls.append(1) or ["private-example-value"])
    payload = [{"text": "private-example-value", "nested": ["safe"]} for _ in range(1000)]
    result = redact_values(payload)
    assert len(calls) == 1
    assert all(row["text"] == "[REDACTED]" for row in result)
    assert redact_values("private-example-value") == "[REDACTED]"
    assert len(calls) == 2  # Credentials are refreshed on each independent write.


@pytest.mark.parametrize("stop", [False, True])
def test_stream_coalesces_snapshots_and_flushes_all_text(tmp_path, monkeypatch, stop):
    import lab.directed_engine as engine
    import lab.directed_gate as gate
    monkeypatch.setattr(gate, "parent_commit", lambda _: COMMIT)
    raw = contract()
    raw["agent_plan"]["lanes"] = raw["agent_plan"]["lanes"][:1]
    raw["budget"]["max_retries_per_call"] = 0
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    root = tmp_path / "state"
    writes = []
    real_write = engine.write

    def record(path, data):
        if path.name == "PARTIAL.json":
            writes.append(1)
        return real_write(path, data)

    monkeypatch.setattr(engine, "write", record)

    class Burst(FakeProvider):
        def call(self, lane, prompt, callback, cancelled, timeout, step_id):
            for _ in range(1000):
                callback("reasoning", "abc")
            if stop:
                (root / raw["project_id"] / "stop.flag").touch()
                callback("content", "last fragment")
            return {"content": '{"findings":"answer"}', "complete": True, "usage": {"cost_usd": 0}}

    result = run_directed_task(path, provider=Burst(), root=root)
    partial = json.loads((Path(result.artifact_root) / "lanes/0/PARTIAL.json").read_text())
    assert partial["reasoning"] == "abc" * 1000
    assert len(writes) < 20
    assert result.status == ("STOPPED" if stop else "COMPLETED_WITH_OPEN_CLAIMS")
    if stop:
        assert partial["content"] == "last fragment"
