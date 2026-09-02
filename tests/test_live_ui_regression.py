import json
from datetime import datetime, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lab.integrity import ProjectRunLock
from lab.project_manager import ProjectManager
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


def test_main_page_shows_research_loop_stage_progress_and_timeline(tmp_path, monkeypatch):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    import lab.openrouter_catalog as catalog

    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda: [])

    pm = ProjectManager()
    info = pm.create_project(
        title="Live loop",
        project_id="live-loop",
        problem="test problem",
        experiment="Araştırma Döngüsü",
    )
    pm.set_active(info.project_id)
    root = pm.project_root(info.project_id)
    lock = ProjectRunLock(root)
    lock.acquire()
    try:
        pm.touch(info.project_id, experiment="Araştırma Döngüsü", status="RUNNING")
        now = datetime.now(timezone.utc).isoformat()
        runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        runtime.update(
            {
                "status": "RUNNING",
                "current_step": "Tur 1/1 · Sceptik · eleştiri",
                "current_agent": "Sceptik",
                "heartbeat_at": now,
                "updated_at": now,
            }
        )
        (root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        (root / "worker_request.json").write_text(
            json.dumps(
                {
                    "request_version": 2,
                    "project_id": info.project_id,
                    "project_uuid": info.project_uuid,
                    "experiment_method": "research_loop",
                    "experiment_name": "Araştırma Döngüsü",
                    "agents": [],
                    "optional_agents": {},
                    "param": 1,
                    "prompt": "test problem",
                }
            ),
            encoding="utf-8",
        )

        run_dir = tmp_path / "runs" / "run-live-loop"
        run_dir.mkdir(parents=True)
        events = [
            {
                "ts": "2026-09-02T17:39:00+00:00",
                "type": "stage",
                "method": "research_loop",
                "label": "İlk çözüm · Teorisyen",
                "index": 1,
                "total": 4,
                "agent": "Teorisyen",
                "model": "fake/model",
                "reasoning_effort": "medium",
                "step_key": "loop:initial:proposer",
            },
            {
                "ts": "2026-09-02T17:39:10+00:00",
                "type": "stage_end",
                "method": "research_loop",
                "label": "İlk çözüm · Teorisyen",
                "index": 1,
                "total": 4,
                "agent": "Teorisyen",
                "step_key": "loop:initial:proposer",
                "total_tokens": 120,
                "reasoning_tokens": 80,
                "cost_usd": 0.001,
                "latency_s": 10.0,
            },
            {
                "ts": now,
                "type": "stage",
                "method": "research_loop",
                "label": "Tur 1/1 · Sceptik · eleştiri",
                "index": 2,
                "total": 4,
                "agent": "Sceptik",
                "model": "fake/model",
                "reasoning_effort": "medium",
                "step_key": "loop:1:critic",
            },
            {
                "ts": now,
                "type": "agent_start",
                "agent": "Sceptik",
                "model": "fake/model",
                "reasoning_effort": "medium",
                "step_key": "loop:1:critic",
                "system_prompt": "critic",
                "prompt": "critic task",
            },
        ]
        (run_dir / "trace.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )
        (run_dir / "stream.jsonl").write_text(
            json.dumps(
                {
                    "ts": now,
                    "type": "agent_stream",
                    "agent": "Sceptik",
                    "model": "fake/model",
                    "reasoning_effort": "medium",
                    "step_key": "loop:1:critic",
                    "channel": "reasoning",
                    "delta": "reasoning text",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        runs_dir = tmp_path / "runs"
        (runs_dir / "index.jsonl").write_text(
            json.dumps(
                {
                    "ts": now,
                    "event": "run_context",
                    "run_id": "run-live-loop",
                    "run_dir": str(run_dir),
                    "project_id": info.project_id,
                    "project_uuid": info.project_uuid,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        at = AppTest.from_file(str(app_path), default_timeout=10).run()
        assert not at.exception
        markdown_values = [element.value for element in at.markdown]
        caption_values = [element.value for element in at.caption]
        assert any("Araştırma Döngüsü · live-loop" in value for value in markdown_values)
        assert any("Tur 1/1 · Sceptik · eleştiri" in value for value in markdown_values)
        assert any("İlk çözüm · Teorisyen" in value for value in caption_values)
        assert any("İlerleme · 2/4" in str(element) for element in at.progress)
    finally:
        lock.release()
