from lab.code_experiment import CODE_EXPERIMENT_SYSTEM_PROMPT, GuardedExperimentWorkspace, WorkspaceActionResult
from lab.prompts import proposal_prompt
from lab.tool_registry import ToolRegistry
from lab.tools import ResearchToolbox, ToolResult


def test_proposal_prompt_lists_checked_in_scripts_and_routes_authored_code(tmp_path):
    scripts = tmp_path / "research_tools"
    scripts.mkdir()
    (scripts / "allowed.py").write_text("print(1)\n", encoding="utf-8")
    toolbox = ResearchToolbox(
        script_root=scripts,
        problem_pack_root=None,
        lean_root=tmp_path / "formal",
    )
    registry = ToolRegistry(toolbox)
    registry.register("code_experiment", lambda _request: ToolResult(True, "code_experiment"))

    prompt = proposal_prompt("P", "L", "ledger", "task", registry)

    assert "checked-in .py dosyaları: allowed.py" in prompt
    assert "Kendi yazdığın/yeni kod için `script` değil `code_experiment` kullan." in prompt


def test_run_python_recovers_safe_relative_script_from_args(tmp_path, monkeypatch):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    called = {}

    def fake_run(path, args=None):
        called["path"] = path
        called["args"] = args
        return WorkspaceActionResult(True, "run_python", metadata={"evidence_level": "COMPUTATION_ONLY"})

    monkeypatch.setattr(workspace, "run_python", fake_run)
    result = workspace.execute(
        {"action": "run_python", "args": ["./collatz_check.py", "7"]}
    )

    assert result.ok
    assert called == {"path": "./collatz_check.py", "args": ["7"]}
    assert result.metadata["normalized_path_from_args"] is True


def test_run_python_missing_path_has_schema_specific_error(tmp_path):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    result = workspace.execute({"action": "run_python", "args": ["--limit", "7"]})

    assert not result.ok
    assert '"path" alanı gerekli' in result.error
    assert '"action":"run_python","path":"exp_001.py","args":[]' in result.error


def test_code_experiment_prompt_predeclares_path_and_dunder_rules():
    assert '{"action":"run_python","path":"exp_001.py","args":[]}' in CODE_EXPERIMENT_SYSTEM_PROMPT
    assert "script dosya adını `args` içine koyma" in CODE_EXPERIMENT_SYSTEM_PROMPT
    assert "__name__" in CODE_EXPERIMENT_SYSTEM_PROMPT
    assert "Dunder" in CODE_EXPERIMENT_SYSTEM_PROMPT
