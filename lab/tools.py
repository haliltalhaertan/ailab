from __future__ import annotations

import json
import os
from itertools import product
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    tool: str
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class ScriptTool:
    """Runs only Python scripts already checked into an approved directory.

    LLM-generated arbitrary Python is intentionally not executed. API keys and
    most environment variables are not forwarded to the child process.
    """

    def __init__(self, root: str | Path = "research_tools", timeout_s: int = 30):
        self.root = Path(root).resolve()
        self.timeout_s = timeout_s

    def _resolve(self, name: str) -> Path:
        candidate = (self.root / name).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Script path research_tools dışına çıkamaz.") from exc
        if candidate.suffix != ".py":
            raise ValueError("Yalnızca .py araştırma scriptleri çalıştırılabilir.")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def run(self, name: str, args: list[str] | None = None) -> ToolResult:
        try:
            script = self._resolve(name)
        except Exception as exc:
            return ToolResult(False, "script", error=str(exc))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *(args or [])],
                cwd=str(self.root),
                env=env,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, "script", error=f"timeout ({self.timeout_s}s): {exc}")
        return ToolResult(
            proc.returncode == 0,
            "script",
            output=proc.stdout.strip(),
            error=proc.stderr.strip(),
            metadata={"returncode": proc.returncode, "script": name, "args": args or []},
        )


class Z3Tool:
    """Deterministic SMT-LIB checker; does not execute generated OS code."""

    def check(self, smt2: str) -> ToolResult:
        try:
            import z3
        except ImportError:
            return ToolResult(False, "z3", error="z3-solver kurulu değil")
        try:
            solver = z3.Solver()
            solver.add(z3.parse_smt2_string(smt2))
            result = solver.check()
            model = str(solver.model()) if result == z3.sat else ""
            return ToolResult(
                True,
                "z3",
                output=json.dumps({"result": str(result), "model": model}, ensure_ascii=False),
                metadata={"result": str(result)},
            )
        except Exception as exc:
            return ToolResult(False, "z3", error=str(exc))


class LeanTool:
    """Optional Lean checker.

    For safety, arbitrary Lean text execution is disabled. Only checked-in .lean
    files under the configured root can be checked.
    """

    def __init__(self, root: str | Path = "formal", timeout_s: int = 60):
        self.root = Path(root).resolve()
        self.timeout_s = timeout_s

    def check_file(self, name: str) -> ToolResult:
        lean = shutil.which("lean")
        if not lean:
            return ToolResult(False, "lean", error="lean binary PATH üzerinde bulunamadı")
        candidate = (self.root / name).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return ToolResult(False, "lean", error="Lean path formal/ dışına çıkamaz")
        if candidate.suffix != ".lean" or not candidate.is_file():
            return ToolResult(False, "lean", error=f"Lean dosyası bulunamadı: {candidate}")
        try:
            proc = subprocess.run(
                [lean, str(candidate)],
                cwd=str(self.root),
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, "lean", error=f"timeout ({self.timeout_s}s): {exc}")
        return ToolResult(
            proc.returncode == 0,
            "lean",
            output=proc.stdout.strip(),
            error=proc.stderr.strip(),
            metadata={"returncode": proc.returncode, "file": name},
        )


class TropicalGridTool:
    """Finite exact grid checker for min-plus circuits on complete graphs.

    This is a counterexample finder, not a general proof engine. A PASS means the
    candidate matched the reference shortest/simple-path value on every tested
    finite weight assignment.
    """

    @staticmethod
    def _edges(n: int) -> list[tuple[int, int]]:
        return [(u, v) for u in range(1, n + 1) for v in range(u + 1, n + 1)]

    @staticmethod
    def _reference(n: int, weights: dict[tuple[int, int], int]) -> int:
        inf = 10**18
        dist = [inf] * (n + 1)
        used = [False] * (n + 1)
        dist[1] = 0
        for _ in range(n):
            u = min((i for i in range(1, n + 1) if not used[i]), key=lambda i: dist[i])
            used[u] = True
            for v in range(1, n + 1):
                if u == v or used[v]:
                    continue
                e = (u, v) if u < v else (v, u)
                dist[v] = min(dist[v], dist[u] + weights[e])
        return dist[n]

    @staticmethod
    def _evaluate(circuit: dict[str, Any], weights: dict[tuple[int, int], int]) -> int:
        values: dict[str, int] = {}
        for gate in circuit.get("gates", []):
            gid = str(gate["id"])
            if gid in values:
                raise ValueError(f"duplicate gate id: {gid}")
            op = str(gate.get("op", "")).lower()
            if op == "edge":
                u, v = int(gate["u"]), int(gate["v"])
                e = (u, v) if u < v else (v, u)
                values[gid] = weights[e]
            elif op in {"add", "plus"}:
                args = [values[str(x)] for x in gate.get("args", [])]
                if len(args) < 2:
                    raise ValueError(f"{gid}: add en az iki arg ister")
                values[gid] = sum(args)
            elif op in {"min", "minimum"}:
                args = [values[str(x)] for x in gate.get("args", [])]
                if len(args) < 2:
                    raise ValueError(f"{gid}: min en az iki arg ister")
                values[gid] = min(args)
            else:
                raise ValueError(f"{gid}: bilinmeyen op {op}")
        out = str(circuit.get("output", ""))
        if out not in values:
            raise ValueError("output gate bulunamadı")
        return values[out]

    def check(
        self,
        circuit: dict[str, Any],
        weight_values: list[int] | None = None,
        max_cases: int = 200_000,
    ) -> ToolResult:
        try:
            n = int(circuit.get("n", 0))
            if n < 2 or n > 7:
                raise ValueError("grid checker için 2 <= n <= 7 gerekli")
            domain = sorted(set(int(x) for x in (weight_values or [0, 1, 2])))
            if not domain or any(x < 0 for x in domain):
                raise ValueError("weight_values nonnegative tamsayı olmalı")
            edges = self._edges(n)
            cases = len(domain) ** len(edges)
            if cases > max_cases:
                raise ValueError(
                    f"{cases} grid case çok büyük; max_cases={max_cases}. "
                    "Daha küçük n veya weight domain kullan."
                )
            checked = 0
            for assignment in product(domain, repeat=len(edges)):
                weights = dict(zip(edges, assignment))
                expected = self._reference(n, weights)
                actual = self._evaluate(circuit, weights)
                checked += 1
                if actual != expected:
                    payload = {
                        "status": "COUNTEREXAMPLE",
                        "n": n,
                        "weights": {f"{u}-{v}": weights[(u, v)] for u, v in edges},
                        "expected": expected,
                        "actual": actual,
                        "cases_checked": checked,
                    }
                    return ToolResult(
                        False,
                        "tropical_grid",
                        output=json.dumps(payload, ensure_ascii=False),
                        metadata=payload,
                    )
            payload = {
                "status": "GRID_PASS",
                "n": n,
                "domain": domain,
                "cases_checked": checked,
                "warning": "Finite grid pass is not a proof over all weights.",
            }
            return ToolResult(
                True,
                "tropical_grid",
                output=json.dumps(payload, ensure_ascii=False),
                metadata=payload,
            )
        except Exception as exc:
            return ToolResult(False, "tropical_grid", error=str(exc))


class ResearchToolbox:
    def __init__(
        self,
        script_root: str | Path = "research_tools",
        lean_root: str | Path = "formal",
    ):
        self.scripts = ScriptTool(script_root)
        self.z3 = Z3Tool()
        self.lean = LeanTool(lean_root)
        self.tropical_grid = TropicalGridTool()

    def execute(self, request: dict[str, Any] | None) -> ToolResult | None:
        if not request:
            return None
        tool = str(request.get("tool", "none")).lower()
        if tool in {"", "none"}:
            return None
        if tool == "script":
            return self.scripts.run(str(request.get("name", "")), [str(x) for x in request.get("args", [])])
        if tool == "z3":
            return self.z3.check(str(request.get("smt2", "")))
        if tool == "lean":
            return self.lean.check_file(str(request.get("file", "")))
        if tool == "tropical_grid":
            circuit = request.get("circuit")
            if not isinstance(circuit, dict):
                return ToolResult(False, "tropical_grid", error="circuit JSON object gerekli")
            domain = request.get("weights")
            weights = [int(x) for x in domain] if isinstance(domain, list) else None
            return self.tropical_grid.check(circuit, weights)
        return ToolResult(False, tool, error=f"Bilinmeyen tool: {tool}")
