import json

from lab.project_manager import ProjectManager


def test_create_activate_archive_clone_and_legacy(tmp_path):
    state_root = tmp_path / "research_state"
    runs_root = tmp_path / "runs"
    pm = ProjectManager(state_root, runs_root)

    project = pm.create_project(
        title="Tropical Circuit",
        project_id="tropical-circuit",
        problem="prove a lower bound",
        description="test project",
        literature_query="tropical circuit lower bound",
    )
    assert project.project_id == "tropical-circuit"
    assert pm.active_project_id() == "tropical-circuit"
    assert (state_root / "tropical-circuit" / "project.json").exists()
    assert (state_root / "tropical-circuit" / "problem_frozen.json").exists()

    pm.archive("tropical-circuit", True)
    assert pm.active_project_id() is None
    assert pm.get("tropical-circuit").archived is True

    clone = pm.clone("tropical-circuit", title="Tropical Copy", new_project_id="tropical-copy")
    assert clone.problem == "prove a lower bound"
    assert clone.project_id == "tropical-copy"
    assert clone.run_count == 0

    legacy = state_root / "legacy-project"
    legacy.mkdir()
    (legacy / "problem_frozen.json").write_text(
        json.dumps({"problem": "old problem", "frozen_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (legacy / "state.json").write_text(
        json.dumps({"items": [], "events": []}), encoding="utf-8"
    )
    found = pm.get("legacy-project")
    assert found.problem == "old problem"
    assert "Legacy" in found.title


def test_project_run_totals_are_scoped_by_project(tmp_path):
    pm = ProjectManager(tmp_path / "research_state", tmp_path / "runs")
    pm.create_project(title="A", project_id="a", problem="p")
    run = tmp_path / "runs" / "20260101_theorem"
    run.mkdir(parents=True)
    (run / "trace.jsonl").write_text(
        json.dumps({"type": "project_context", "project_id": "a"}) + "\n",
        encoding="utf-8",
    )
    (run / "summary.json").write_text(
        json.dumps({"total_tokens": 1234, "total_cost_usd": 0.42, "total_calls": 4}),
        encoding="utf-8",
    )
    info = pm.get("a")
    assert info.run_count == 1
    assert info.total_tokens == 1234
    assert info.total_cost_usd == 0.42
