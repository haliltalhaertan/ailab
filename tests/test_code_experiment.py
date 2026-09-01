import json

from lab.code_experiment import GuardedExperimentWorkspace, WorkspaceActionResult


def test_workspace_writes_runs_and_captures_outputs(tmp_path, fake_container_runtime):
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

    command = fake_container_runtime[-1]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert any(part.startswith("--memory=") for part in command)
    assert any(part.startswith("--pids-limit=") for part in command)
    assert any(part.startswith("--cpus=") for part in command)


def test_workspace_fails_closed_without_container_engine(tmp_path, monkeypatch):
    import lab.code_experiment as code_experiment

    monkeypatch.setattr(code_experiment.shutil, "which", lambda name: None)
    ws = GuardedExperimentWorkspace(tmp_path / "workspace", timeout_s=5, container_engine="")
    assert ws.write_file("exp.py", "print('x')\n").ok
    result = ws.run_python("exp.py")
    assert not result.ok
    assert "container" in result.error.lower()


def test_workspace_rejects_path_escape_and_unsafe_imports(tmp_path):
    ws = GuardedExperimentWorkspace(tmp_path / "workspace")
    outside = ws.write_file("../escape.py", "print('x')")
    assert not outside.ok
    assert "workspace" in outside.error.lower()

    unsafe = ws.write_file("bad.py", "import os\nprint(os.listdir('.'))\n")
    assert not unsafe.ok
    assert "import" in unsafe.error.lower()

    alias_open = ws.write_file("alias.py", "o = open\no('outside.txt', 'w')\n")
    assert not alias_open.ok
    assert "isim" in alias_open.error.lower()

    alias_eval = ws.write_file("eval_alias.py", "e = eval\nprint(e('1+1'))\n")
    assert not alias_eval.ok
    assert "isim" in alias_eval.error.lower()


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
