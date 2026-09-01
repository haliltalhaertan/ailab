import json

from lab.code_experiment import GuardedExperimentWorkspace, WorkspaceActionResult


def test_workspace_writes_runs_and_captures_outputs(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace", timeout_s=5)
    written = ws.write_file(
        "exp_001.py",
        "from itertools import combinations\nprint(sum(1 for _ in combinations(range(5), 2)))\n",
    )
    assert written.ok

    result = ws.run_python("exp_001.py")
    assert result.ok
    assert result.output.strip() == "10"
    assert result.metadata["returncode"] == 0
    assert result.metadata["evidence_level"] == "COMPUTATION_ONLY"
    assert (ws.root / result.metadata["stdout_file"]).read_text(encoding="utf-8").strip() == "10"


def test_workspace_rejects_path_escape_and_unsafe_imports(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace")
    outside = ws.write_file("../escape.py", "print('x')")
    assert not outside.ok
    assert "workspace" in outside.error.lower()

    unsafe = ws.write_file("bad.py", "import os\nprint(os.listdir('.'))\n")
    assert not unsafe.ok
    assert "import" in unsafe.error.lower()


def test_workspace_patch_requires_unique_match(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace")
    assert ws.write_file("exp.py", "x = 1\nprint(x)\n").ok
    patched = ws.patch_file("exp.py", "x = 1", "x = 2")
    assert patched.ok
    assert "x = 2" in ws.read_file("exp.py").output


def test_workspace_action_dispatch(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace")
    result = ws.execute({"action": "write_file", "path": "notes.txt", "content": "abc"})
    assert isinstance(result, WorkspaceActionResult)
    assert result.ok
    listed = ws.execute({"action": "list_files"})
    assert listed.ok
    assert any(x["path"] == "notes.txt" for x in json.loads(listed.output))
