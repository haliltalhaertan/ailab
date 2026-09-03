from __future__ import annotations

import json
from pathlib import Path

import lab.ui_project_settings as ui
from lab.integrity import atomic_write_json


def test_project_ui_settings_round_trip_models_and_effort(tmp_path: Path):
    root = tmp_path / "research_state" / "p"
    root.mkdir(parents=True)

    path = ui.save_project_ui_settings(
        root,
        agents={
            "Theorist": {
                "model": "example/theorist",
                "reasoning_effort": "high",
            }
        },
        orchestrator_default={
            "model": "example/generic",
            "reasoning_effort": "medium",
        },
    )

    assert path == root / "ui_settings.json"
    assert ui.configured_model(root, "Theorist") == "example/theorist"
    assert ui.configured_effort(root, "Theorist") == "high"
    assert ui.configured_model(root, "UnknownRole") == "example/generic"
    assert ui.configured_effort(root, "UnknownRole") == "medium"


def test_local_storage_summary_uses_absolute_paths(tmp_path: Path):
    root = tmp_path / "research_state" / "p"
    runs = tmp_path / "runs"
    summary = ui.local_storage_summary(root, runs)

    assert Path(summary["project_root"]).is_absolute()
    assert Path(summary["runs_root"]).is_absolute()
    assert summary["latest_result"].endswith("worker_result.md")
    assert summary["checkpoints"].endswith("checkpoints")


def test_delete_run_history_removes_run_dirs_and_index_rows(tmp_path: Path):
    runs = tmp_path / "runs"
    run_a = runs / "run-a"
    run_b = runs / "run-b"
    run_other = runs / "run-other"
    for path in (run_a, run_b, run_other):
        path.mkdir(parents=True)
        (path / "summary.json").write_text("{}", encoding="utf-8")
    (runs / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"run_id": "run-a", "event": "run_context"}),
                json.dumps({"run_id": "run-b", "event": "run_context"}),
                json.dumps({"run_id": "run-other", "event": "run_context"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    deleted = ui.delete_run_history(
        [
            {"run": "run-a", "run_dir": str(run_a)},
            {"run": "run-b", "run_dir": str(run_b)},
        ],
        runs,
    )

    assert deleted == 2
    assert not run_a.exists()
    assert not run_b.exists()
    assert run_other.exists()
    index = (runs / "index.jsonl").read_text(encoding="utf-8")
    assert "run-a" not in index
    assert "run-b" not in index
    assert "run-other" in index


def test_force_stop_marks_dead_worker_interrupted(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    atomic_write_json(
        root / "runtime.json",
        {
            "status": "RUNNING",
            "heartbeat_at": "2026-09-03T00:00:00+00:00",
            "updated_at": "2026-09-03T00:00:00+00:00",
        },
    )

    monkeypatch.setattr(ui, "_worker_pid", lambda _root: 12345)
    monkeypatch.setattr(ui, "process_alive", lambda _pid: False)
    monkeypatch.setattr(ui.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.os, "killpg", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ui.os, "kill", lambda *args, **kwargs: None)

    assert ui.force_stop_worker(root, wait_s=0.0) is True
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "INTERRUPTED"


def test_force_stop_fails_closed_when_pid_survives(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "run.lock").write_text("live", encoding="utf-8")
    atomic_write_json(root / "runtime.json", {"status": "RUNNING"})

    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(ui, "_worker_pid", lambda _root: 12345)
    monkeypatch.setattr(ui, "process_alive", lambda _pid: True)
    monkeypatch.setattr(ui.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(ui.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ui.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui.os, "killpg", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ui.os, "kill", lambda *args, **kwargs: None)

    assert ui.force_stop_worker(root, wait_s=0.0) is False
    assert (root / "run.lock").exists()
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "RUNNING"


def test_project_ui_source_exposes_model_first_storage_and_delete_controls():
    repo = Path(__file__).resolve().parents[1]
    projects = (repo / "pages" / "1_Projeler.py").read_text(encoding="utf-8")
    app = (repo / "app.py").read_text(encoding="utf-8")

    assert "Araştırmayı yapacak LLM'leri seç" in projects
    assert "LLM kullanmadan elle kur" in projects
    assert "save_project_ui_settings" in projects
    assert "HER ŞEYİ SİL · run logları dahil" in projects
    assert "Yerel kayıt:" in projects
    assert "configured_model(PROJECTS.project_root(active.project_id), role)" in app
    assert "ZORLA DURDUR · HEMEN" in app
    assert "Bu proje bu bilgisayara otomatik kaydediliyor" in app
