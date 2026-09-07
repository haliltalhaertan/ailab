"""Regression tests for Denetim 15 findings G-1..G-5 on the PR G semantic bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lab.agent import Agent
from lab.code_experiment import CodeExperimentRunner, GuardedExperimentWorkspace, WorkspaceActionResult, strip_top_level_redefinitions
from lab.integrity import content_fingerprint, sha256_file
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.tools import ToolResult
from lab.trace import Trace


class NoopClient:
    pass


def _agent() -> Agent:
    return Agent("CodeExperimentAgent", "", model="fake/model", client=NoopClient())


def _lab(tmp_path: Path):
    trace = Trace("audit15", out_dir=tmp_path / "runs")
    state = ResearchState(tmp_path / "project")
    lab = TheoremResearchLab(trace, state)
    return lab, trace, state


def _fake_success(workspace: GuardedExperimentWorkspace, text: str = "RAW STDOUT\n"):
    output = workspace.outputs / "fake.stdout.txt"
    output.write_bytes(text.encode("utf-8"))
    script = workspace.root / "exp_001.py"
    if not script.exists():
        script.write_text("print('x')\n", encoding="utf-8")
    return WorkspaceActionResult(
        True,
        "run_python",
        output=text.strip(),
        metadata={
            "path": "exp_001.py",
            "stdout_file": "outputs/fake.stdout.txt",
            "stdout_sha256": sha256_file(output),
            "script_sha256": sha256_file(script),
            "evidence_level": "COMPUTATION_ONLY",
        },
    )


def _runner(tmp_path: Path, monkeypatch, *, max_steps: int = 6):
    workspace = GuardedExperimentWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace, "refresh_execution_availability", lambda: True)
    trace = Trace("audit15-runner", out_dir=tmp_path / "runs")
    runner = CodeExperimentRunner(workspace, trace, max_steps=max_steps)
    return workspace, trace, runner


def _run_scripted(runner, workspace, actions: list[dict], *, source: str, symbols: list[str]):
    responses = iter(json.dumps(action) for action in actions)

    def call_agent(_agent, _prompt, _step):
        return next(responses)

    def execute(_key, action):
        if action["action"] == "run_python":
            return _fake_success(workspace)
        return workspace.execute(action)

    return runner.run(
        agent=_agent(),
        task="run requested check",
        step_key="iter:1:tool",
        call_agent=call_agent,
        execute_cached=execute,
        source=source,
        definitions_file="definitions.py",
        definition_symbols=symbols,
    )


# ---------------------------------------------------------------- G-1


def test_g1_agent_cannot_write_or_patch_definitions(tmp_path, monkeypatch):
    workspace, trace, runner = _runner(tmp_path, monkeypatch)
    canonical = "def sigma(n):\n    return n + 1\n"
    workspace.write_file("definitions.py", canonical)
    result = _run_scripted(
        runner,
        workspace,
        [
            {"action": "write_file", "path": "definitions.py", "content": "def sigma(n):\n    return 0\n"},
            {"action": "patch_file", "path": "./definitions.py", "old": "n + 1", "new": "n + 2"},
            {"action": "run_python", "path": "exp_001.py", "args": []},
            {"action": "finish", "summary": "done"},
        ],
        source="print(sigma(60))",
        symbols=["sigma"],
    )
    trace.close()
    assert result.ok is True
    assert (workspace.root / "definitions.py").read_text(encoding="utf-8") == canonical
    text = trace.path.read_text(encoding="utf-8")
    assert text.count('"protected_definitions": true') == 2


def test_g1_engine_rejects_experiment_when_definitions_changed(tmp_path, monkeypatch):
    lab, trace, state = _lab(tmp_path)
    lab.code_agent = _agent()
    item = state.add_item("conjecture", "candidate", "sigma(n) <= 10")
    request = lab._bind_code_definitions(
        proposal={"definitions": {"sigma": "def sigma(n):\n    return n + 1"}},
        item_id=item.id,
        request={"tool": "code_experiment", "task": "check", "source": "print(sigma(1))"},
    )
    definitions_path = state.root / "workspace" / "definitions.py"
    expected_sha = request["_definitions_sha256"]
    assert sha256_file(definitions_path) == expected_sha

    def tampering_run(**kwargs):
        definitions_path.write_text("def sigma(n):\n    return 0\n", encoding="utf-8")
        return ToolResult(True, "code_experiment", output="0\n", metadata={"status": "EXPERIMENT_COMPLETE"})

    monkeypatch.setattr(lab.code_runner, "run", tampering_run)
    result = lab._run_code_experiment(request, "iter:1:tool")
    assert result.ok is False
    assert result.metadata["status"] == "DEFINITIONS_TAMPERED"
    assert result.metadata["definitions_sha256"] == expected_sha
    assert result.metadata["definitions_actual_sha256"] == sha256_file(definitions_path)
    assert '"definitions_tampered"' in trace.path.read_text(encoding="utf-8")

    def honest_run(**kwargs):
        definitions_path.write_text("def sigma(n):\n    return n + 1\n", encoding="utf-8")
        return ToolResult(True, "code_experiment", output="2\n", metadata={"status": "EXPERIMENT_COMPLETE"})

    monkeypatch.setattr(lab.code_runner, "run", honest_run)
    result = lab._run_code_experiment(request, "iter:1:tool")
    assert result.ok is True
    assert result.metadata["definitions_sha256"] == expected_sha
    assert result.metadata["definitions_file"] == "definitions.py"
    assert result.metadata["definition_symbols"] == ["sigma"]
    trace.close()


# ---------------------------------------------------------------- G-2


def test_g2_identical_redefinition_is_stripped_not_rejected(tmp_path, monkeypatch):
    workspace, trace, runner = _runner(tmp_path, monkeypatch)
    workspace.write_file("definitions.py", "def sigma(n):\n    return n + 1\n")
    source = "import math\n\ndef sigma(n):\n    return n + 1\n\n\ndef L(n):\n    return 2\n\nprint(sigma(60), L(3))\n"
    result = _run_scripted(
        runner,
        workspace,
        [
            {"action": "run_python", "path": "exp_001.py", "args": []},
            {"action": "finish", "summary": "done"},
        ],
        source=source,
        symbols=["sigma"],
    )
    trace.close()
    assert result.ok is True, result.error
    written = (workspace.root / "exp_001.py").read_text(encoding="utf-8")
    assert written.startswith("from definitions import sigma\n")
    assert "def sigma" not in written
    assert "def L(n):" in written and "import math" in written
    text = trace.path.read_text(encoding="utf-8")
    assert '"stripped_redefinitions": ["sigma"]' in text


def test_g2_assignment_rebinding_still_fails_closed(tmp_path, monkeypatch):
    workspace, trace, runner = _runner(tmp_path, monkeypatch)
    workspace.write_file("definitions.py", "def sigma(n):\n    return n + 1\n")
    result = _run_scripted(
        runner,
        workspace,
        [{"action": "finish", "summary": "never reached"}],
        source="sigma = lambda n: 0\nprint(sigma(1))\n",
        symbols=["sigma"],
    )
    trace.close()
    assert result.ok is False
    assert result.metadata["status"] == "SOURCE_REDEFINES_DEFINITION"
    assert result.metadata["symbols"] == ["sigma"]


def test_g2_strip_helper_keeps_decorators_and_other_code():
    source = "@staticmethod\ndef sigma(n):\n    return 1\n\nclass Keep:\n    pass\n"
    kept, stripped, remaining = strip_top_level_redefinitions(source, ["sigma"])
    assert stripped == ["sigma"] and remaining == []
    assert kept == "\nclass Keep:\n    pass\n"
    kept, stripped, remaining = strip_top_level_redefinitions("from definitions import sigma\nprint(sigma(1))\n", ["sigma"])
    assert stripped == [] and remaining == []


# ---------------------------------------------------------------- G-3

REAL_NEXT_TASKS = [
    "code_experiment ile critic'in iki triviallik iddiasını deterministic doğrula: (i) tüm n∈[2,2^18], k∈{8,12,16} için "
    "σ(n)≥⌈log₂n⌉ olduğunu ve L(a(n),k)≤min(k,σ(n)-1)≤⌈log₂n⌉ olduğunu hesapla; (ii) rastgele örneklemde a ile a+2^k'nın "
    "ilk k parite vektörlerinin Terras olgusuna göre birebir eşleştiğini doğrulayarak mevcut exp_001 çıktısının kod/claim "
    "uyumsuzluğunu kesinleştir.",
    "exp_005.py: (1) σ semantiğini σ_total (1'e ulaşma adımı) olarak sabitle ve bunu çıktıda açıkça raporla; (2) P1'i yalnızca "
    "σ_total yorumunda, tüm n∈[2,2^18] için σ(n)≥⌈log₂n⌉ ihlal sayısıyla deterministik çalıştır; (3) P2 halkalarını AYRI AYRI "
    "raporla: L_emp(a,k)≤min(k,σ(n)-1) ve min(k,σ(n)-1)≤⌈log₂n⌉; (4) P3'ü sabit tohumlu küçük örneklemeyle teyit et; (5) tek JSON özeti üret.",
    "exp_006: exp_005'i dal mantığı ve σ_total semantiği düzeltilerek yeniden yaz ve code_experiment ile doğrula. (1) σ_total'ı "
    "doğrudan yörünge sayımından hesapla ve n∈{3,7,27,60,97} için değerleri sabit referansla assert et (σ(3)=7, σ(7)=16, σ(60)=19). "
    "(3) min(k,σ(n)-1)≤⌈log₂n⌉ zincirini iddia olarak koyma; bunun yerine L_emp dağılımını k ve n dilimlerinde raporla.",
]


def _seed_unresolved(state: ResearchState, *, verdicts=("INCONCLUSIVE", "INCONCLUSIVE")) -> None:
    decisions = ("KILL", "REVISE")
    for index, (decision, verdict) in enumerate(zip(decisions, verdicts)):
        state.add_item(
            "conjecture",
            f"tur {index + 1}",
            f"claim {index + 1}",
            status="REFUTATION_CANDIDATE",
            metadata={
                "target_id": "T1",
                "input_next_task": REAL_NEXT_TASKS[index - 1] if index else "initial task",
                "manager_decision": decision,
                "manager_next_task": REAL_NEXT_TASKS[index],
                "verifier_verdict": verdict,
            },
        )


def test_g3_structural_brake_fires_on_real_third_turn(tmp_path):
    lab, trace, state = _lab(tmp_path)
    _seed_unresolved(state)
    current = state.add_item("conjecture", "tur 3", "claim 3", metadata={"target_id": "T1"})
    warning = lab._repeated_next_task_warning(current_item_id=current.id, current_task=REAL_NEXT_TASKS[1], target_id="T1")
    assert warning
    text = trace.path.read_text(encoding="utf-8")
    assert '"repeated_next_task"' in text
    assert '"unresolved_streak": 2' in text
    trace.close()


def test_g3_brake_stays_quiet_after_verifier_pass_or_single_turn(tmp_path):
    lab, trace, state = _lab(tmp_path)
    _seed_unresolved(state, verdicts=("PASS", "INCONCLUSIVE"))
    current = state.add_item("conjecture", "tur 3", "claim 3", metadata={"target_id": "T1"})
    assert lab._repeated_next_task_warning(current_item_id=current.id, current_task="brand new direction: lower bound via tropical rank", target_id="T1") == ""

    other = ResearchState(tmp_path / "other")
    other.add_item(
        "conjecture",
        "tur 1",
        "claim",
        metadata={"target_id": "T1", "input_next_task": "start", "manager_decision": "REVISE", "manager_next_task": "unrelated task", "verifier_verdict": "INCONCLUSIVE"},
    )
    single = TheoremResearchLab(Trace("single", out_dir=tmp_path / "runs2"), other)
    current = other.add_item("conjecture", "tur 2", "claim 2", metadata={"target_id": "T1"})
    assert single._repeated_next_task_warning(current_item_id=current.id, current_task="unrelated task", target_id="T1") == ""
    trace.close()


def test_g3_weighted_similarity_prefers_identifiers():
    same_theme = TheoremResearchLab._weighted_task_similarity(REAL_NEXT_TASKS[1], REAL_NEXT_TASKS[2])
    unrelated = TheoremResearchLab._weighted_task_similarity(REAL_NEXT_TASKS[1], "prove the lower bound with a rank argument over the monomial lattice")
    assert same_theme > unrelated
    assert unrelated < TheoremResearchLab.REPETITION_SIMILARITY_THRESHOLD


# ---------------------------------------------------------------- G-5


def test_g5_finish_payload_carries_text_sha_matching_output(tmp_path, monkeypatch):
    workspace, trace, runner = _runner(tmp_path, monkeypatch)
    workspace.write_file("definitions.py", "def sigma(n):\n    return n + 1\n")
    result = _run_scripted(
        runner,
        workspace,
        [{"action": "run_python", "path": "exp_001.py", "args": []}, {"action": "finish", "summary": "done"}],
        source="print(sigma(1))",
        symbols=["sigma"],
    )
    trace.close()
    assert result.ok is True
    assert result.metadata["stdout_text_sha256"] == hashlib.sha256(result.output.encode("utf-8")).hexdigest()


def test_g5_engine_reuses_cached_stdout_with_crlf_bytes(tmp_path):
    lab, trace, _state = _lab(tmp_path)
    request = {"tool": "code_experiment", "task": "check"}
    step_key = "iter:1:tool"
    raw_bytes = b"line one\r\nline two\r\n"
    output = raw_bytes.decode("utf-8").replace("\r\n", "\n")
    lab._cache_put(
        step_key,
        {
            "status": "COMPLETE",
            "fingerprint": content_fingerprint("tool_step:v3", request),
            "result": {
                "ok": True,
                "tool": "code_experiment",
                "output": output,
                "error": "",
                "metadata": {
                    "status": "EXPERIMENT_COMPLETE",
                    "stdout_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "stdout_text_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                },
            },
        },
    )
    reused = lab._tool(request, step_key)
    assert reused is not None and reused.ok is True and reused.output == output
    text = trace.path.read_text(encoding="utf-8")
    assert '"code_experiment_stdout_cache_invalidated"' not in text
    assert '"step_reused"' in text
    trace.close()
