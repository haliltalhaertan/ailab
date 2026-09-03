import gzip
import json
from types import SimpleNamespace

from lab.trace import Trace
from lab.ui_live import build_cards
from lab import ui_model


def _line(value):
    return json.dumps(value, ensure_ascii=False) + "\n"


def test_gzip_stream_tail_and_since_are_read_via_raw_path(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    raw = run_dir / "stream.jsonl"
    rows = [
        {"type": "agent_stream", "step_key": "a", "channel": "reasoning", "delta": "old"},
        {"type": "agent_stream", "step_key": "a", "channel": "reasoning", "delta": "middle"},
        {"type": "agent_stream", "step_key": "a", "channel": "content", "delta": "recent"},
    ]
    payload = "".join(_line(row) for row in rows).encode("utf-8")
    with gzip.open(str(raw) + ".gz", "wb") as handle:
        handle.write(payload)

    tail = ui_model.read_jsonl_tail(raw, max_bytes=180)
    assert tail[-1]["delta"] == "recent"
    events, offset = ui_model.read_jsonl_since(raw, 0)
    assert [event["delta"] for event in events] == ["old", "middle", "recent"]
    assert offset == len(payload)
    assert ui_model.read_jsonl_since(raw, offset) == ([], offset)


def test_trace_compress_stream_roundtrip(tmp_path):
    trace = Trace("archive", out_dir=tmp_path / "runs")
    trace.log(
        "agent_stream",
        agent="A",
        model="fake/model",
        reasoning_effort="medium",
        step_key="s",
        channel="reasoning",
        delta="reasoning text",
    )
    trace.close()
    gz_path = trace.compress_stream()

    assert gz_path == trace.run_dir / "stream.jsonl.gz"
    assert gz_path.is_file()
    assert not trace.stream_path.exists()
    events = ui_model.read_jsonl_tail(trace.stream_path)
    assert events[0]["channel"] == "reasoning"
    assert events[0]["delta"] == "reasoning text"


def test_history_trace_only_loader_does_not_touch_stream_and_keeps_full_cards(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    llm = {
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "llm_call",
        "agent": "Theorist",
        "model": "fake/model",
        "output": "FULL ANSWER",
        "provider_reasoning": "FULL REASONING",
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 3,
        "latency_s": 1.0,
    }
    start = {
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "agent_start",
        "agent": "Theorist",
        "model": "fake/model",
        "step_key": "iter:1:proposer",
    }
    (run_dir / "trace.jsonl").write_text(_line(start) + _line(llm), encoding="utf-8")
    (run_dir / "stream.jsonl").write_text(_line({"type": "SHOULD_NOT_BE_READ"}), encoding="utf-8")

    seen = []
    original = ui_model.read_jsonl

    def spy(path):
        seen.append(path.name)
        return original(path)

    monkeypatch.setattr(ui_model, "read_jsonl", spy)
    events = ui_model.load_run_events(run_dir, include_stream=False)
    cards = build_cards(events)

    assert seen == ["trace.jsonl"]
    assert cards[0].reasoning == "FULL REASONING"
    assert cards[0].content == "FULL ANSWER"


def test_app_history_explicitly_disables_stream_loading():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "load_run_events(selected, include_stream=False)" in source
