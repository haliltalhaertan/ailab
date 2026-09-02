from pathlib import Path

from lab.tools import ScriptTool, TropicalGridTool, Z3Tool


def test_script_tool_runs_only_checked_in_script(tmp_path: Path):
    root = tmp_path / "research_tools"
    root.mkdir()
    script = root / "echo.py"
    script.write_text("import sys; print('OK:' + ','.join(sys.argv[1:]))", encoding="utf-8")
    tool = ScriptTool(root)
    result = tool.run("echo.py", ["a", "b"])
    assert result.ok
    assert result.output == "OK:a,b"


def test_script_tool_blocks_path_traversal(tmp_path: Path):
    root = tmp_path / "research_tools"
    root.mkdir()
    tool = ScriptTool(root)
    result = tool.run("../evil.py")
    assert not result.ok
    assert "dışına" in result.error


def test_script_tool_runs_from_secondary_trusted_root(tmp_path: Path):
    primary = tmp_path / "research_tools"
    secondary = tmp_path / "problem_packs"
    primary.mkdir()
    pack = secondary / "toy"
    pack.mkdir(parents=True)
    (pack / "check.py").write_text("print('PACK_OK')", encoding="utf-8")

    tool = ScriptTool(primary, trusted_roots=[secondary])
    result = tool.run("toy/check.py")

    assert result.ok
    assert result.output == "PACK_OK"


def test_script_tool_rejects_parent_segments_across_trusted_roots(tmp_path: Path):
    primary = tmp_path / "research_tools"
    secondary = tmp_path / "problem_packs"
    primary.mkdir()
    pack = secondary / "toy"
    pack.mkdir(parents=True)
    (pack / "check.py").write_text("print('PACK_OK')", encoding="utf-8")

    tool = ScriptTool(primary, trusted_roots=[secondary])
    result = tool.run("../problem_packs/toy/check.py")

    assert not result.ok
    assert "dışına" in result.error


def test_script_tool_rejects_ambiguous_relative_name(tmp_path: Path):
    primary = tmp_path / "research_tools"
    secondary = tmp_path / "problem_packs"
    primary.mkdir()
    secondary.mkdir()
    (primary / "same.py").write_text("print('PRIMARY')", encoding="utf-8")
    (secondary / "same.py").write_text("print('SECONDARY')", encoding="utf-8")

    tool = ScriptTool(primary, trusted_roots=[secondary])
    result = tool.run("same.py")

    assert not result.ok
    assert "birden fazla" in result.error


def test_z3_tool_returns_unsat():
    result = Z3Tool().check(
        """
        (declare-const x Int)
        (assert (> x 5))
        (assert (< x 3))
        """
    )
    assert result.ok
    assert result.metadata["result"] == "unsat"


def test_tropical_grid_checker_accepts_k3_shortest_path_circuit():
    circuit = {
        "n": 3,
        "gates": [
            {"id": "e13", "op": "edge", "u": 1, "v": 3},
            {"id": "e12", "op": "edge", "u": 1, "v": 2},
            {"id": "e23", "op": "edge", "u": 2, "v": 3},
            {"id": "via2", "op": "add", "args": ["e12", "e23"]},
            {"id": "out", "op": "min", "args": ["e13", "via2"]},
        ],
        "output": "out",
    }
    result = TropicalGridTool().check(circuit, [0, 1, 2])
    assert result.ok
    assert result.metadata["cases_checked"] == 27
    assert result.metadata["gate_count"] == 2
    assert result.metadata["edge_gate_count"] == 3
    assert "monomial" in result.metadata["warning"].lower()


def test_tropical_grid_checker_finds_functional_counterexample():
    bad = {
        "n": 3,
        "gates": [{"id": "e13", "op": "edge", "u": 1, "v": 3}],
        "output": "e13",
    }
    result = TropicalGridTool().check(bad, [0, 1, 2])
    assert not result.ok
    assert result.metadata["status"] == "COUNTEREXAMPLE"
    assert result.metadata["gate_count"] == 0
    assert result.metadata["edge_gate_count"] == 1
