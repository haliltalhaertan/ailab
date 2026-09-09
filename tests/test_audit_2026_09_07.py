"""Regression checks for complete statements, legacy evidence and OS ownership."""

import subprocess
import sys

import pytest

from lab.integrity import ProjectBusyError, ProjectRunLock
from lab.status_guard import choose_status
from lab.tools import LeanTool, ToolResult


@pytest.mark.parametrize("binders", ["(h : False)", "{h : False}", "(n : Nat) (h : False)"])
def test_assumptions_cannot_be_omitted(tmp_path, binders):
    tool = LeanTool(root=tmp_path / "formal")
    source = f"theorem bound {binders} : False := h"
    args = dict(theorem_name="bound", item_id="C-1", iteration=1, claim_hash="a" * 64)
    assert not tool.draft_source("bad.lean", source, theorem_type="False", **args).ok
    assert tool.draft_source("ok.lean", source, theorem_type=f"∀ {binders}, False", **args).ok


def test_legacy_formal_flags_cannot_promote():
    metadata = dict(formal_verified=True, source_clean=True, axioms_verified=True,
                    formal_binding_verified=True, claim_hash="a" * 64)
    result = ToolResult(True, "lean", metadata=metadata)
    decision = choose_status("PROVEN", tool_result=result, verifier={"verdict": "PASS"},
                             critic={"verdict": "PASS"}, expected_claim_hash="a" * 64)
    assert decision.granted != "PROVEN"


def test_os_owner_survives_missing_metadata_and_releases_after_crash(tmp_path):
    script = """
import sys
from lab.integrity import ProjectRunLock
lock = ProjectRunLock(sys.argv[1]).acquire()
print('ready', flush=True)
sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        # Even a removed metadata file cannot admit another process.
        (tmp_path / "run.lock").unlink()
        with pytest.raises(ProjectBusyError):
            ProjectRunLock(tmp_path).acquire()
    finally:
        child.kill()
        child.communicate(timeout=10)
    with ProjectRunLock(tmp_path):
        assert (tmp_path / "run.lock").exists()


def test_crashed_owner_metadata_is_reclaimed(tmp_path):
    code = "from lab.integrity import ProjectRunLock; import sys; ProjectRunLock(sys.argv[1]).acquire()"
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, timeout=10)
    with ProjectRunLock(tmp_path):
        assert (tmp_path / "run.lock").exists()
