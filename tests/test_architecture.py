from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
PACK_ROOT = REPO_ROOT / "problem_packs"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> list[tuple[str, bool]]:
    modules: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((alias.name, False) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module:
                modules.append((module, node.level > 0))
            elif node.level > 0:
                modules.extend((alias.name, True) for alias in node.names)
    return modules


def test_lab_does_not_import_problem_packs():
    offenders: list[str] = []
    for path in sorted(LAB_ROOT.rglob("*.py")):
        for module, _relative in _imported_modules(_tree(path)):
            if module == "problem_packs" or module.startswith("problem_packs."):
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {module}")
    assert offenders == [], "lab must not import domain problem packs: " + ", ".join(offenders)


def _pack_local_imports(path: Path, pack_dir: Path) -> set[str]:
    tree = _tree(path)
    local_stems = {candidate.stem for candidate in pack_dir.glob("*.py")}
    imported: set[str] = set()
    for module, relative in _imported_modules(tree):
        terminal = module.split(".")[-1]
        if relative and terminal in local_stems:
            imported.add(terminal)
            continue
        if module.startswith(f"problem_packs.{pack_dir.name}.") and terminal in local_stems:
            imported.add(terminal)
            continue
        if module in local_stems:
            imported.add(module)
    return imported


def test_problem_pack_search_and_check_paths_stay_independent():
    if not PACK_ROOT.exists():
        return

    for pack_dir in sorted(path for path in PACK_ROOT.iterdir() if path.is_dir()):
        assert (pack_dir / "README.md").is_file(), f"{pack_dir.name} must contain README.md"
        protected = {
            path.stem
            for path in pack_dir.glob("*.py")
            if path.stem.startswith(("search", "check"))
            or path.stem == "common"
            or path.stem.endswith("_semantics")
        }
        for script in sorted(
            path
            for path in pack_dir.glob("*.py")
            if path.stem.startswith(("search", "check"))
        ):
            forbidden = protected - {script.stem}
            overlap = _pack_local_imports(script, pack_dir) & forbidden
            assert not overlap, (
                f"{script.relative_to(REPO_ROOT)} must be an independent trust path; "
                f"pack-local imports are forbidden here: {sorted(overlap)}"
            )


def _subprocess_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    module_aliases: set[str] = set()
    popen_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name == "Popen":
                    popen_aliases.add(alias.asname or "Popen")
    return module_aliases, popen_aliases


def _popen_calls(tree: ast.Module) -> list[ast.Call]:
    module_aliases, popen_aliases = _subprocess_aliases(tree)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "Popen"
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ) or (isinstance(func, ast.Name) and func.id in popen_aliases):
            calls.append(node)
    return calls


def _worker_launcher_is_pinned(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "command" for target in node.targets):
            continue
        value = node.value
        if not isinstance(value, ast.List):
            continue
        constants = [item.value for item in value.elts if isinstance(item, ast.Constant)]
        if "-m" in constants and "lab.worker" in constants:
            return True
    return False


def test_generated_code_popen_is_confined_to_code_experiment():
    offenders: list[str] = []
    for path in sorted(LAB_ROOT.rglob("*.py")):
        tree = _tree(path)
        calls = _popen_calls(tree)
        if not calls or path.name == "code_experiment.py":
            continue
        if path.name == "worker_launcher.py" and _worker_launcher_is_pinned(tree):
            continue
        offenders.extend(f"{path.relative_to(REPO_ROOT)}:{call.lineno}" for call in calls)

    assert offenders == [], (
        "subprocess.Popen outside code_experiment must be a pinned worker launch, never generated code: "
        + ", ".join(offenders)
    )


def test_streamlit_entrypoints_do_not_import_orchestrator():
    entrypoints = [REPO_ROOT / "app.py", *sorted((REPO_ROOT / "pages").glob("*.py"))]
    offenders: list[str] = []
    for path in entrypoints:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "lab.orchestrator" for alias in node.names):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                if node.module == "lab.orchestrator":
                    offenders.append(str(path.relative_to(REPO_ROOT)))
                if node.module == "lab" and any(alias.name == "Orchestrator" for alias in node.names):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], "Streamlit entrypoints must launch workers instead of Orchestrator: " + ", ".join(offenders)
