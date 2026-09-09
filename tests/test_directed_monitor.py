"""Monitor reads public run records only; no models or external service calls."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lab.directed_monitor import (
    discover_runs, heartbeat_warning, lane_names, lane_snapshot, load_roots,
    read_json, register_root,
)


def make_run(root, name="directed-one"):
    folder = root / "project" / "directed" / name
    folder.mkdir(parents=True)
    (folder / "runtime.json").write_text(json.dumps({"status": "RUNNING", "active_lanes": {"sol": {}}, "usage": {"provider_cost_complete": False}}))
    lane = folder / "lanes" / "sol"
    lane.mkdir(parents=True)
    (lane / "PARTIAL.json").write_text(json.dumps({"reasoning": "thinking", "content": "", "reasoning_details": []}))
    return folder


def test_discovery_external_roots_and_arbitrary_lane_names(tmp_path):
    one, two = tmp_path / "main", tmp_path / "external"
    folder = make_run(one)
    make_run(two, "directed-two")
    (folder / "lanes" / "custom-scientist").mkdir()
    rows = discover_runs([one, two, one])
    assert len(rows) == 2
    assert lane_names(one, folder, {"completed_lanes": ["unknown"]}) == ["custom-scientist", "sol", "unknown"]
    assert lane_snapshot(one, folder, "sol")["reasoning"] == "thinking"
    assert rows[0]["runtime"]["usage"]["provider_cost_complete"] is False


def test_registration_persists_explicit_paths(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    config = tmp_path / "config" / "roots.json"
    register_root(config, str(root))
    register_root(config, str(root))
    assert load_roots(config) == [root.resolve()]
    with pytest.raises(ValueError):
        register_root(config, ".")


def test_partial_json_is_visible_error_not_silent_missing(tmp_path):
    folder = make_run(tmp_path)
    (folder / "runtime.json").write_text('{"status":')
    assert discover_runs([tmp_path])[0]["error"]
    with pytest.raises(ValueError):
        read_json(tmp_path, folder / "runtime.json")


def test_secret_files_never_read_and_path_escape_rejected(tmp_path):
    folder = make_run(tmp_path / "runs")
    (folder / ".env").write_bytes(b"not JSON secret")
    assert lane_snapshot(tmp_path / "runs", folder, "sol")["reasoning"] == "thinking"
    with pytest.raises(ValueError):
        lane_snapshot(tmp_path / "runs", folder, "../../outside")
    outside = tmp_path / "outside.json"
    outside.write_text('{}')
    with pytest.raises(ValueError):
        read_json(tmp_path / "runs", outside)


def test_stale_heartbeat_and_terminal_not_live():
    now = datetime.now(timezone.utc)
    assert heartbeat_warning({"status": "RUNNING", "heartbeat_at": (now - timedelta(seconds=40)).isoformat()}, now)
    assert not heartbeat_warning({"status": "RUNNING", "heartbeat_at": now.isoformat()}, now)
    assert not heartbeat_warning({"status": "STOPPED"}, now)


def test_response_supersedes_partial(tmp_path):
    folder = make_run(tmp_path)
    (folder / "lanes" / "sol" / "RESPONSE.json").write_text(json.dumps({"provider_reasoning": "complete reasoning", "content": "answer"}))
    assert lane_snapshot(tmp_path, folder, "sol")["content"] == "answer"


def test_unstarted_planned_lanes_are_visible(tmp_path):
    folder = make_run(tmp_path)
    (folder / "TASK_CONTRACT.json").write_text(json.dumps({"agent_plan": {"lanes": [{"lane_id": "waiting"}]}}))
    assert lane_names(tmp_path, folder, {}) == ["sol", "waiting"]
    assert lane_snapshot(tmp_path, folder, "waiting") == {}


def test_monitor_page_displays_reasoning_and_uncertain_cost(tmp_path):
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    page = tmp_path / "pages" / "8_Canli_Gorevler.py"
    page.parent.mkdir()
    source = (root / "pages" / page.name).read_text(encoding="utf-8")
    page.write_text(source, encoding="utf-8")
    make_run(tmp_path / "research_state")
    app = AppTest.from_file(str(page), default_timeout=20).run()
    assert not app.exception
    assert any("thinking" in item.value for item in app.code)
    assert any("kesin değil" in item.value for item in app.warning)
    assert len(app.selectbox) == 2
    next(button for button in app.button if button.label == "Araştırma teslim özetini hazırla").click().run()
    assert not app.exception
    assert app.session_state["live_report"]
    app.run()
    assert not app.exception
    assert any("düğmeye basıldığı" in item.value for item in app.caption)
    assert any(item.label == "Teslim özetini indir" for item in app.get("download_button"))
