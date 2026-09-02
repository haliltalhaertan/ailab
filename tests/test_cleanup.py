from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lab.step_store import StepStore
from lab.tool_registry import ToolRegistry


def test_malformed_iteration_snapshot_payload_is_tolerated(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(
        1,
        ledger_revision="rev",
        ledger_context="context",
        payload={"ok": True},
    )
    with sqlite3.connect(store.path) as con:
        con.execute(
            "UPDATE iteration_snapshots SET payload_json=? WHERE iteration=?",
            ("{broken-json", 1),
        )

    snapshot = store.get_iteration_snapshot(1)
    assert snapshot is not None
    assert snapshot["iteration"] == 1
    assert snapshot["ledger_revision"] == "rev"
    assert snapshot["ledger_context"] == "context"
    assert "ok" not in snapshot


def test_tool_registry_names_has_one_canonical_shape():
    registry = ToolRegistry()
    names = registry.names()
    assert names[0] == "none"
    assert set(names[1:]) == set(registry.BUILTIN_NAMES)
    with pytest.raises(TypeError):
        registry.names(include_none=False)  # type: ignore[call-arg]


def test_legacy_theorem_lab_shims_are_removed():
    lab_root = Path(__file__).resolve().parents[1] / "lab"
    for name in (
        "theorem_lab.py",
        "hardened_theorem_lab.py",
        "partial_resume_theorem_lab.py",
        "resumable_theorem_lab.py",
        "code_experiment_theorem_lab.py",
    ):
        assert not (lab_root / name).exists(), name


def test_psutil_is_not_a_declared_dependency():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "psutil" not in pyproject
