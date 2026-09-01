import json
from pathlib import Path

from lab.ui_model import load_live_run_events


def test_sidebar_agent_config_is_not_emitted_by_streamlit_magic():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "(optional if is_optional else agents if False else agents)" not in source


def test_live_loader_keeps_trace_and_only_recent_stream_tail(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace_event = {"ts": "2026-01-01T00:00:00", "type": "agent_start", "agent": "Theorist"}
    (run_dir / "trace.jsonl").write_text(json.dumps(trace_event) + "\n", encoding="utf-8")

    old = {"ts": "2026-01-01T00:00:01", "type": "agent_stream", "delta": "OLD"}
    recent = {"ts": "2026-01-01T00:00:02", "type": "agent_stream", "delta": "RECENT"}
    padding = {"ts": "2026-01-01T00:00:01.5", "type": "agent_stream", "delta": "x" * 6000}
    stream_text = "\n".join(json.dumps(x) for x in (old, padding, recent)) + "\n"
    (run_dir / "stream.jsonl").write_text(stream_text, encoding="utf-8")

    events = load_live_run_events(run_dir, stream_tail_bytes=1000)
    assert trace_event in events
    assert any(event.get("delta") == "RECENT" for event in events)
    assert not any(event.get("delta") == "OLD" for event in events)
