from pathlib import Path

from lab.prompts import manager_prompt, proposal_prompt, proposal_schema, verifier_prompt
from lab.tool_registry import ToolRegistry
from lab.tools import ResearchToolbox


def _registry(tmp_path: Path) -> ToolRegistry:
    scripts = tmp_path / "research_tools"
    scripts.mkdir()
    return ToolRegistry(
        ResearchToolbox(
            script_root=scripts,
            problem_pack_root=None,
            lean_root=tmp_path / "formal",
        )
    )


def test_lean_is_hidden_when_host_execution_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ALLOW_HOST_LEAN", raising=False)
    monkeypatch.setattr("lab.tool_registry.shutil.which", lambda _name: "C:/fake/lean.exe")
    registry = _registry(tmp_path)

    assert registry.is_available("lean_draft") is False
    assert "lean_draft" not in registry.names(available_only=True)
    assert "lean_draft" not in proposal_schema(registry)["tool_request"]["tool"].split("|")
    prompt = proposal_prompt("P", "L", "ledger", "task", registry)
    assert "formal doğrulama (Lean) YOK" in prompt
    assert "Lean taslağı yazma" in prompt


def test_lean_is_exposed_only_when_enabled_and_binary_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr(
        "lab.tool_registry.shutil.which",
        lambda name: "C:/fake/lean.exe" if name in {"lean", "lake"} else None,
    )
    registry = _registry(tmp_path)

    assert registry.is_available("lean_draft") is True
    assert "lean_draft" in registry.names(available_only=True)
    prompt = proposal_prompt("P", "L", "ledger", "task", registry)
    assert "tam olarak BİR top-level theorem/lemma" in prompt
    assert "`have`" in prompt


def test_frozen_effective_snapshot_cannot_be_widened_by_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr("lab.tool_registry.shutil.which", lambda _name: "C:/fake/lean.exe")
    registry = _registry(tmp_path)
    assert registry.availability()["lean_draft"]["available"] is True

    registry.set_effective_availability(
        {
            "lean_draft": {"available": False, "reason": "run başında kapalı"},
            "script": {"available": True, "reason": "declared"},
            "z3": {"available": True, "reason": "declared"},
            "tropical_grid": {"available": True, "reason": "declared"},
        }
    )

    assert registry.is_available("lean_draft") is False
    result = registry.execute({"tool": "lean_draft", "source": "theorem x : True := by trivial"})
    assert result is not None
    assert result.ok is False
    assert result.metadata["tool_unavailable"] is True
    assert "run başında kapalı" in result.error


def test_runtime_loss_narrows_a_frozen_available_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr("lab.tool_registry.shutil.which", lambda _name: None)
    registry = _registry(tmp_path)
    registry.set_effective_availability(
        {
            "lean_draft": {"available": True, "reason": "declared open"},
            "script": {"available": True, "reason": "declared"},
            "tropical_grid": {"available": True, "reason": "declared"},
        }
    )

    row = registry.effective_availability()["lean_draft"]
    assert row["available"] is False
    assert "PATH" in row["reason"]


def test_verifier_prompt_defines_tool_failures_as_inconclusive(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ALLOW_HOST_LEAN", raising=False)
    registry = _registry(tmp_path)
    prompt = verifier_prompt(
        "P",
        "C-1",
        {"claim": "x"},
        {"ok": False, "tool": "lean", "error": "format"},
        registry,
    )

    assert "FAIL = deterministic counterexample/refutation" in prompt
    assert "INCONCLUSIVE = tool unavailable, timeout, syntax/format error" in prompt
    assert "tool failure is NEVER" in prompt


def test_manager_does_not_request_formal_promotion_when_lean_is_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ALLOW_HOST_LEAN", raising=False)
    registry = _registry(tmp_path)
    prompt = manager_prompt(
        "P",
        "C-1",
        "claim",
        None,
        {"verdict": "INCONCLUSIVE"},
        {"verdict": "KEEP"},
        registry=registry,
    )

    assert "PROOF_CANDIDATE/PROVEN isteme" in prompt
    assert "Lean ortamı gerektirir" in prompt
