"""Follow-up review of 3a31136. Assertions describe required safe behavior."""
import socket

import lab.integrity as integrity
from lab.integrity import ProjectRunLock, atomic_write_json, sha256_file
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.tools import LeanTool
from lab.trace import Trace


def test_r01_assumptions_cannot_be_dropped_from_expected_type(tmp_path):
    tool = LeanTool(tmp_path / "formal")
    source = "theorem bound (h : False) : False := h\n"
    result = tool.draft_source(
        "bound.lean", source, theorem_name="bound", theorem_type="False",
        item_id="C-review", iteration=1, claim_hash="a" * 64,
    )
    assert not result.ok, "False -> False was accepted as the unconditional expected proposition False"


def test_r02_reclaim_does_not_open_a_window_for_a_third_owner(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    old = {"pid": 99999999, "host": socket.gethostname(), "token": "old"}
    atomic_write_json(root / "run.lock", old)
    first, second, third = (ProjectRunLock(root) for _ in range(3))
    # First has read `old`; second finishes reclaiming it before first proceeds.
    second.acquire()
    original_replace = integrity.os.replace

    def replace_then_third_acquires(src, dst):
        original_replace(src, dst)
        if str(src) == str(root / "run.lock") and ".reclaim-" in str(dst):
            third.acquire()

    monkeypatch.setattr(integrity.os, "replace", replace_then_third_acquires)
    try:
        assert first._reclaim_stale(old) is False
        assert not (second.acquired and third.acquired), "Moving a live lock created an admission window for a third worker"
    finally:
        first.release()
        second.release()
        third.release()


def test_r03_legacy_formal_cache_is_checked_against_new_statement_gate(tmp_path):
    state = ResearchState(tmp_path / "project")
    trace = Trace("review-cache", tmp_path / "runs")
    lab = TheoremResearchLab(trace, state)
    source = "def unrelated : Prop := 1 = 2\ntheorem bound : True := True.intro\n"
    candidate = state.root / "formal" / "candidates" / "legacy.lean"
    candidate.write_text(source, encoding="utf-8")
    raw = {"ok": True, "tool": "lean", "metadata": {
        "file": candidate.name, "lean_sha256": sha256_file(candidate),
        "theorem_name": "bound", "theorem_type": "1 = 2",
        "formal_verified": True, "source_clean": True,
        "axioms_verified": True, "formal_binding_verified": True,
    }}
    try:
        assert not lab.toolbox.lean._guard_source(source, "bound", "1 = 2")[0]
        reused = lab._cached_formal_result(raw)
        assert not reused.ok, "Legacy formal cache bypasses the new statement gate"
    finally:
        trace.close()
