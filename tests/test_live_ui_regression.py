import json
from datetime import datetime, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lab.integrity import ProjectRunLock
from lab.project_manager import ProjectManager
from lab.ui_model import load_live_run_delta, load_live_run_events, live_log_offsets


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


def test_live_loader_reads_only_new_jsonl_records_after_offset(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "trace.jsonl"
    stream = run_dir / "stream.jsonl"
    trace.write_text(json.dumps({"type": "stage", "step_key": "a"}) + "\n", encoding="utf-8")
    stream.write_text(json.dumps({"type": "agent_stream", "step_key": "a", "delta": "old"}) + "\n", encoding="utf-8")
    offsets = live_log_offsets(run_dir)

    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "stage_end", "step_key": "a"}) + "\n")
    with stream.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "agent_stream", "step_key": "b", "delta": "new"}) + "\n")

    events, new_offsets = load_live_run_delta(run_dir, offsets)

    assert [event.get("type") for event in events] == ["stage_end", "agent_stream"]
    assert not any(event.get("delta") == "old" for event in events)
    assert any(event.get("delta") == "new" for event in events)
    assert new_offsets["trace"] > offsets["trace"]
    assert new_offsets["stream"] > offsets["stream"]


def _prepare_live_project(tmp_path: Path, *, completed_card: bool):
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
        if completed_card:
            events.append(
                {
                    "ts": now,
                    "type": "llm_call",
                    "agent": "Sceptik",
                    "model": "fake/model",
                    "output": "final critique",
                    "provider_reasoning": "final reasoning",
                    "prompt_tokens": 20,
                    "completion_tokens": 30,
                    "reasoning_tokens": 10,
                    "cached_tokens": 0,
                    "total_tokens": 50,
                    "cost_usd": 0.001,
                    "latency_s": 1.0,
                }
            )
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
    except Exception:
        lock.release()
        raise
    return pm, info, root, lock


def test_main_page_shows_research_loop_stage_progress_and_timeline(tmp_path, monkeypatch):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    import lab.openrouter_catalog as catalog

    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda: [])
    _pm, _info, _root, lock = _prepare_live_project(tmp_path, completed_card=False)
    try:
        at = AppTest.from_file(str(app_path), default_timeout=10).run()
        assert not at.exception
        subheader_values = [element.value for element in at.subheader]
        markdown_values = [element.value for element in at.markdown]
        caption_values = [element.value for element in at.caption]
        assert any("Araştırma Döngüsü · live-loop" in value for value in subheader_values)
        assert any("Tur 1/1 · Sceptik · eleştiri" in value for value in markdown_values)
        assert any("İlk çözüm · Teorisyen" in value for value in caption_values)
        assert len(at.get("progress")) >= 1
    finally:
        lock.release()


def test_completed_live_card_header_stays_done_across_fragment_reruns(tmp_path, monkeypatch):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    import lab.openrouter_catalog as catalog

    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda: [])
    _pm, _info, _root, lock = _prepare_live_project(tmp_path, completed_card=True)
    try:
        at = AppTest.from_file(str(app_path), default_timeout=10).run()
        assert not at.exception
        first_labels = [getattr(element, "label", "") for element in at.get("expander")]
        assert any(label.startswith("✅ Sceptik") for label in first_labels)
        assert not any(label.startswith("⏳ Sceptik") for label in first_labels)

        at = at.run()
        assert not at.exception
        second_labels = [getattr(element, "label", "") for element in at.get("expander")]
        assert any(label.startswith("✅ Sceptik") for label in second_labels)
        assert not any(label.startswith("⏳ Sceptik") for label in second_labels)
    finally:
        lock.release()


def test_live_worker_replaces_run_action_with_stop_even_when_heartbeat_is_stale(tmp_path, monkeypatch):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    import lab.openrouter_catalog as catalog

    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda: [])
    _pm, _info, root, lock = _prepare_live_project(tmp_path, completed_card=False)
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    runtime["heartbeat_at"] = "2026-01-01T00:00:00+00:00"
    runtime["updated_at"] = runtime["heartbeat_at"]
    (root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    try:
        at = AppTest.from_file(str(app_path), default_timeout=10).run()
        assert not at.exception
        labels = [button.label for button in at.button]
        assert "DURDUR" in labels
        assert "Deneyi Çalıştır" not in labels
        assert any("STALE_RUNNING" in caption.value for caption in at.caption)

        stop = next(button for button in at.button if button.label == "DURDUR")
        at = stop.click().run()
        assert not at.exception
        assert (root / "stop.flag").exists()
        labels = [button.label for button in at.button]
        assert "Durdurma isteği gönderildi…" in labels
    finally:
        lock.release()


def test_completed_cards_render_outside_fragment_and_active_preview_is_bounded(tmp_path, monkeypatch):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    source = app_path.read_text(encoding="utf-8")
    assert "@st.fragment(run_every=1.0)\ndef _render_live_fragment" in source
    assert "@st.fragment(run_every=1.0)\ndef render_live_run" not in source

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    import lab.openrouter_catalog as catalog

    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda: [])
    _pm, _info, _root, lock = _prepare_live_project(tmp_path, completed_card=False)
    run_dir = tmp_path / "runs" / "run-live-loop"
    trace_path = run_dir / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    for idx in range(3):
        agent = f"DoneAgent{idx}"
        step = f"done:{idx}"
        events.extend(
            [
                {
                    "ts": f"2026-09-02T17:39:2{idx}+00:00",
                    "type": "agent_start",
                    "agent": agent,
                    "model": "fake/model",
                    "step_key": step,
                    "prompt": "done task",
                },
                {
                    "ts": f"2026-09-02T17:39:3{idx}+00:00",
                    "type": "llm_call",
                    "agent": agent,
                    "model": "fake/model",
                    "output": f"DONE_SECRET_{idx}_" + ("D" * 5000),
                    "provider_reasoning": "done reasoning",
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "reasoning_tokens": 1,
                    "total_tokens": 3,
                    "latency_s": 1.0,
                },
            ]
        )
    trace_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    stream_event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "agent_stream",
        "agent": "Sceptik",
        "model": "fake/model",
        "reasoning_effort": "medium",
        "step_key": "loop:1:critic",
        "channel": "reasoning",
        "delta": "R" * 5000,
    }
    (run_dir / "stream.jsonl").write_text(json.dumps(stream_event) + "\n", encoding="utf-8")

    try:
        at = AppTest.from_file(str(app_path), default_timeout=10).run()
        assert not at.exception
        labels = [getattr(element, "label", "") for element in at.get("expander")]
        done_labels = {label for label in labels if label.startswith("✅ DoneAgent")}
        assert len(done_labels) == 3
        assert any(label.startswith("⏳ Sceptik") for label in labels)
        markdown_values = [element.value for element in at.markdown]
        assert "R" * 4000 in markdown_values
        assert "R" * 4001 not in "\n".join(markdown_values)
        assert not any("DONE_SECRET_" in value for value in markdown_values)
        assert any("toplam 5,000 karakter" in caption.value for caption in at.caption)

        show = next(button for button in at.button if button.label == "Göster")
        at = show.click().run()
        assert not at.exception
        assert any("DONE_SECRET_" in element.value for element in at.markdown)
    finally:
        lock.release()
