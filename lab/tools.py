from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

from lab.integrity import sha256_file


@dataclass
class ToolResult:
    ok: bool
    tool: str
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "tool": self.tool, "output": self.output, "error": self.error, "metadata": self.metadata}


class ScriptTool:
    """Runs only human-reviewed Python scripts checked into research_tools/."""

    def __init__(self, root: str | Path = "research_tools", timeout_s: int = 30):
        self.root = Path(root).resolve()
        self.timeout_s = int(timeout_s)

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
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script), *(args or [])],
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
            metadata={"returncode": proc.returncode, "script": name, "args": args or [], "script_sha256": sha256_file(script)},
        )


class Z3Tool:
    """Deterministic SMT-LIB checker with a hard solver timeout."""

    def __init__(self, timeout_ms: int = 30_000):
        self.timeout_ms = max(100, int(timeout_ms))

    def check(self, smt2: str) -> ToolResult:
        try:
            import z3
        except ImportError:
            return ToolResult(False, "z3", error="z3-solver kurulu değil")
        try:
            solver = z3.Solver()
            solver.set("timeout", self.timeout_ms)
            solver.add(z3.parse_smt2_string(smt2))
            result = solver.check()
            model = str(solver.model()) if result == z3.sat else ""
            return ToolResult(
                result != z3.unknown,
                "z3",
                output=json.dumps({"result": str(result), "model": model}, ensure_ascii=False),
                error="solver timeout/unknown" if result == z3.unknown else "",
                metadata={"result": str(result), "timeout_ms": self.timeout_ms},
            )
        except Exception as exc:
            return ToolResult(False, "z3", error=str(exc), metadata={"timeout_ms": self.timeout_ms})


class LeanTool:
    """Formal proof candidate writer/checker.

    LLMs may write only ``formal/candidates/*.lean``. Successful checking is the
    *only* code path that emits ``formal_verified=True``. Generated Lean is not
    executed unless LAB_ALLOW_HOST_LEAN=1 because Lean can contain metaprogramming.
    For a stricter deployment, point this adapter at a containerized Lean command.
    """

    FORBIDDEN = ("run_cmd", "#eval", "unsafe", "System.", "IO.", "Process", "implemented_by", "@[extern")

    def __init__(self, root: str | Path = "formal", timeout_s: int = 120):
        self.root = Path(root).resolve()
        self.candidates = self.root / "candidates"
        self.candidates.mkdir(parents=True, exist_ok=True)
        self.timeout_s = int(timeout_s)

    def _candidate(self, name: str) -> Path:
        safe_name = Path(str(name or "candidate.lean")).name
        if not safe_name.endswith(".lean"):
            safe_name += ".lean"
        candidate = (self.candidates / safe_name).resolve()
        candidate.relative_to(self.candidates)
        return candidate

    def draft_source(self, name: str, source: str) -> ToolResult:
        try:
            text = str(source or "")
            if not text.strip():
                raise ValueError("Lean source boş olamaz.")
            lowered = text.lower()
            for token in self.FORBIDDEN:
                if token.lower() in lowered:
                    raise ValueError(f"Generated Lean içinde izin verilmeyen yürütme özelliği: {token}")
            target = self._candidate(name)
            target.write_text(text, encoding="utf-8")
            return ToolResult(
                True,
                "lean_draft",
                output=f"Formal candidate written: {target.name}",
                metadata={"file": target.name, "lean_sha256": sha256_file(target), "formal_draft": True},
            )
        except Exception as exc:
            return ToolResult(False, "lean_draft", error=str(exc))

    def check_file(self, name: str) -> ToolResult:
        try:
            candidate = self._candidate(name)
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            if os.environ.get("LAB_ALLOW_HOST_LEAN", "0") != "1":
                return ToolResult(
                    False,
                    "lean",
                    error="Generated Lean host execution is disabled. Set LAB_ALLOW_HOST_LEAN=1 only in a trusted/containerized Lean environment.",
                    metadata={"file": candidate.name, "lean_sha256": sha256_file(candidate), "formal_verified": False},
                )
            lake = shutil.which("lake")
            lean = shutil.which("lean")
            if lake:
                command = [lake, "env", "lean", str(candidate)]
            elif lean:
                command = [lean, str(candidate)]
            else:
                raise RuntimeError("lean/lake binary PATH üzerinde bulunamadı")
            env = {
                key: value
                for key in ("PATH", "HOME", "USERPROFILE", "LEAN_PATH", "XDG_CACHE_HOME", "TEMP", "TMP")
                if (value := os.environ.get(key))
            }
            proc = subprocess.run(
                command,
                cwd=str(self.root),
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
                env=env,
            )
            ok = proc.returncode == 0
            metadata = {
                "returncode": proc.returncode,
                "file": candidate.name,
                "lean_sha256": sha256_file(candidate),
                "formal_verified": ok,
                "checker": "lake env lean" if lake else "lean",
            }
            return ToolResult(ok, "lean", output=proc.stdout.strip(), error=proc.stderr.strip(), metadata=metadata)
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, "lean", error=f"timeout ({self.timeout_s}s): {exc}", metadata={"formal_verified": False})
        except Exception as exc:
            return ToolResult(False, "lean", error=str(exc), metadata={"formal_verified": False})


class TropicalGridTool:
    """Finite exact grid checker for min-plus circuits on complete graphs."""

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

    def check(self, circuit: dict[str, Any], weight_values: list[int] | None = None, max_cases: int = 200_000) -> ToolResult:
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
                raise ValueError(f"{cases} grid case çok büyük; max_cases={max_cases}.")
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
                    return ToolResult(False, "tropical_grid", output=json.dumps(payload, ensure_ascii=False), metadata=payload)
            payload = {
                "status": "GRID_PASS",
                "n": n,
                "domain": domain,
                "cases_checked": checked,
                "warning": "Finite grid pass is not a proof over all weights.",
            }
            return ToolResult(True, "tropical_grid", output=json.dumps(payload, ensure_ascii=False), metadata=payload)
        except Exception as exc:
            return ToolResult(False, "tropical_grid", error=str(exc))


class ResearchToolbox:
    def __init__(self, script_root: str | Path = "research_tools", lean_root: str | Path = "formal"):
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
        if tool == "lean_draft":
            return self.lean.draft_source(str(request.get("file") or "candidate.lean"), str(request.get("source") or ""))
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
