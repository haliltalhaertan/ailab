from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
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
        return {
            "ok": self.ok,
            "tool": self.tool,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }

    def as_evidence(
        self,
        *,
        request: dict[str, Any] | None = None,
        contract=None,
        target_id: str | None = None,
    ):
        from lab.evidence import evidence_from_tool_result

        return evidence_from_tool_result(
            self, request=request, contract=contract, target_id=target_id
        )


class ScriptTool:
    """Runs only checked-in scripts from explicit trusted roots.

    ``research_tools/`` remains the primary root. Production may add
    ``problem_packs/`` as a second human-reviewed root; relative paths are
    resolved independently inside each root and ambiguity fails closed.
    """

    def __init__(
        self,
        root: str | Path = "research_tools",
        timeout_s: int = 30,
        *,
        trusted_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    ):
        roots = [Path(root).resolve()]
        for raw in trusted_roots or ():
            candidate = Path(raw).resolve()
            if candidate not in roots:
                roots.append(candidate)
        self.root = roots[0]
        self.trusted_roots = tuple(roots)
        self.timeout_s = int(timeout_s)

    def available_scripts(self) -> tuple[str, ...]:
        """Return unambiguous checked-in Python paths accepted by this tool."""

        counts: dict[str, int] = {}
        for root in self.trusted_roots:
            if not root.is_dir():
                continue
            for script in root.rglob("*.py"):
                if not script.is_file():
                    continue
                relative = script.relative_to(root).as_posix()
                counts[relative] = counts.get(relative, 0) + 1
        return tuple(sorted(name for name, count in counts.items() if count == 1))

    def _resolve(self, name: str) -> tuple[Path, Path]:
        requested = Path(str(name or ""))
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("Script path trusted roots dışına çıkamaz.")
        if requested.suffix != ".py":
            raise ValueError("Yalnızca .py araştırma scriptleri çalıştırılabilir.")

        matches: list[tuple[Path, Path]] = []
        for root in self.trusted_roots:
            candidate = (root / requested).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                matches.append((candidate, root))

        if len(matches) > 1:
            raise ValueError("Script birden fazla trusted root içinde bulundu; belirsiz yol reddedildi.")
        if matches:
            return matches[0]
        raise FileNotFoundError((self.root / requested).resolve())

    @staticmethod
    def _literal_assignment(tree: ast.Module, name: str):
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
                continue
            value = node.value
            if value is None:
                return None
            try:
                return ast.literal_eval(value)
            except (ValueError, TypeError):
                return None
        return None

    @classmethod
    def _policy(cls, script: Path) -> dict[str, Any]:
        try:
            tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        except (OSError, SyntaxError):
            return {
                "allowed_evidence_kinds": ["INCONCLUSIVE"],
                "accepts_specification": False,
                "evidence_role": "GENERAL",
                "policy_error": "static declarations could not be parsed",
            }
        raw_allowed = cls._literal_assignment(tree, "AILAB_ALLOWED_EVIDENCE_KINDS")
        allowed = (
            {str(value).upper() for value in raw_allowed}
            if isinstance(raw_allowed, (list, tuple, set))
            else {"NUMERICAL_PASS", "INCONCLUSIVE"}
        )
        accepts_specification = cls._literal_assignment(tree, "AILAB_ACCEPTS_SPECIFICATION") is True
        role = str(cls._literal_assignment(tree, "AILAB_EVIDENCE_ROLE") or "GENERAL").upper()
        policy_error = ""
        if "FORMAL_PROOF" in allowed:
            allowed = {"INCONCLUSIVE"}
            policy_error = "checked-in scripts cannot self-declare FORMAL_PROOF"
        if accepts_specification:
            allowed &= {"SOLVER_RESULT", "NUMERICAL_PASS", "INCONCLUSIVE"}
        allowed.add("INCONCLUSIVE")
        if role not in {"SEARCH_CERTIFICATE", "INDEPENDENT_CHECKER", "GENERAL"}:
            role = "GENERAL"
        return {
            "allowed_evidence_kinds": sorted(allowed),
            "accepts_specification": accepts_specification,
            "evidence_role": role,
            "policy_error": policy_error,
        }

    @staticmethod
    def _payload(stdout: str) -> dict[str, Any] | None:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return None
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def run(self, name: str, args: list[str] | None = None) -> ToolResult:
        started = time.monotonic()
        try:
            script, script_root = self._resolve(name)
        except Exception as exc:
            return ToolResult(False, "script", error=str(exc), metadata={"runtime_s": time.monotonic() - started})
        policy = self._policy(script)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script), *(args or [])],
                cwd=str(script_root),
                env=env,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                False,
                "script",
                error=f"timeout ({self.timeout_s}s): {exc}",
                metadata={
                    **policy,
                    "script": name,
                    "args": args or [],
                    "script_sha256": sha256_file(script),
                    "runtime_s": time.monotonic() - started,
                },
            )
        payload = self._payload(proc.stdout)
        metadata = {
            **policy,
            "returncode": proc.returncode,
            "script": name,
            "args": args or [],
            "script_sha256": sha256_file(script),
            "runtime_s": time.monotonic() - started,
        }
        if payload is not None:
            metadata["evidence_payload"] = payload
        return ToolResult(
            proc.returncode == 0,
            "script",
            output=proc.stdout.strip(),
            error=proc.stderr.strip(),
            metadata=metadata,
        )


class Z3Tool:
    """Deterministic SMT-LIB checker with non-empty assertion and timeout gates."""

    def __init__(self, timeout_ms: int = 30_000):
        self.timeout_ms = max(100, int(timeout_ms))

    def _no_assertions(self) -> ToolResult:
        return ToolResult(
            False,
            "z3",
            error="no assertions",
            metadata={
                "result": "inconclusive",
                "timeout_ms": self.timeout_ms,
                "assertion_count": 0,
            },
        )

    def check(self, smt2: str) -> ToolResult:
        if not str(smt2 or "").strip():
            return self._no_assertions()
        try:
            import z3
        except ImportError:
            return ToolResult(False, "z3", error="z3-solver kurulu değil")
        try:
            solver = z3.Solver()
            solver.set("timeout", self.timeout_ms)
            solver.add(z3.parse_smt2_string(smt2))
            assertion_count = len(solver.assertions())
            if assertion_count <= 0:
                return self._no_assertions()
            result = solver.check()
            model = str(solver.model()) if result == z3.sat else ""
            return ToolResult(
                result != z3.unknown,
                "z3",
                output=json.dumps({"result": str(result), "model": model}, ensure_ascii=False),
                error="solver timeout/unknown" if result == z3.unknown else "",
                metadata={
                    "result": str(result),
                    "timeout_ms": self.timeout_ms,
                    "assertion_count": assertion_count,
                },
            )
        except Exception as exc:
            return ToolResult(
                False,
                "z3",
                error=str(exc),
                metadata={
                    "result": "inconclusive",
                    "timeout_ms": self.timeout_ms,
                    "assertion_count": 0,
                },
            )


class LeanTool:
    """Project-local formal proof candidate writer/checker.

    A successful Lean exit code is necessary but not sufficient. Generated source
    is bound to one ledger item/iteration/claim hash and formal statement, escape
    hatches are rejected before execution, compiler warnings are errors, and
    ``#print axioms`` must report only configured trusted Lean axioms.
    """

    FORBIDDEN_PATTERNS = (
        r"\bsorry\b",
        r"\badmit\b",
        r"\baxiom\b",
        r"\bopaque\b",
        r"\bnative_decide\b",
        r"\bset_option\b",
        r"\bpartial\s+def\b",
        r"\brun_cmd\b",
        r"#eval\b",
        r"\bunsafe\b",
        r"\bSystem\s*\.",
        r"\bIO\s*\.",
        r"\bProcess\b",
        r"\bimplemented_by\b",
        r"@\[extern",
    )
    DECLARATION = re.compile(r"(?m)^\s*(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b")
    CLAIM_MARKER = re.compile(r"(?m)^\s*--\s*ailab-claim:\s*([0-9a-f]{64})\s*$", re.I)

    def __init__(self, root: str | Path = "formal", timeout_s: int = 120):
        self.root = Path(root).resolve()
        self.candidates = self.root / "candidates"
        self.candidates.mkdir(parents=True, exist_ok=True)
        self.timeout_s = int(timeout_s)

    @staticmethod
    def _strip_comments(source: str) -> str:
        text = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
        text = re.sub(r"/-.*?-\/", " ", text, flags=re.S)
        text = re.sub(r"--[^\n]*", " ", text)
        return text

    @staticmethod
    def _compact(value: str) -> str:
        return " ".join(str(value or "").split())

    def _guard_source(self, source: str, theorem_name: str, theorem_type: str) -> tuple[bool, str]:
        clean = self._strip_comments(source)
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, clean, flags=re.I):
                return False, f"Generated Lean içinde izin verilmeyen construct: {pattern}"
        declarations = list(self.DECLARATION.finditer(clean))
        if len(declarations) != 1:
            return False, "Formal candidate tam olarak bir theorem/lemma declaration içermelidir."
        if not theorem_name.strip() or not theorem_type.strip():
            return False, "lean_draft için theorem_name ve theorem_type zorunludur."
        if declarations[0].group(2) != theorem_name.strip():
            return False, "Kaynak theorem_name ile binding theorem_name eşleşmiyor."
        compact = self._compact(clean)
        if self._compact(theorem_type) not in compact:
            return False, "Kaynak içinde binding theorem_type bulunamadı."
        return True, ""

    def _candidate(self, name: str) -> Path:
        safe_name = Path(str(name or "candidate.lean")).name
        if not safe_name.endswith(".lean"):
            safe_name += ".lean"
        candidate = (self.candidates / safe_name).resolve()
        candidate.relative_to(self.candidates)
        return candidate

    def draft_source(
        self,
        name: str,
        source: str,
        *,
        theorem_name: str = "",
        theorem_type: str = "",
        item_id: str = "",
        iteration: int | None = None,
        claim_hash: str = "",
        claim_sha256: str = "",
    ) -> ToolResult:
        try:
            text = str(source or "")
            if not text.strip():
                raise ValueError("Lean source boş olamaz.")
            ok, reason = self._guard_source(text, theorem_name, theorem_type)
            if not ok:
                raise ValueError(reason)
            binding_hash = str(claim_hash or claim_sha256).strip().lower()
            if not item_id or iteration is None or not re.fullmatch(r"[0-9a-f]{64}", binding_hash):
                raise ValueError("Formal candidate item_id/iteration/claim hash binding olmadan yazılamaz.")
            text = self.CLAIM_MARKER.sub("", text).lstrip("\n")
            bound_text = f"-- ailab-claim: {binding_hash}\n{text}"
            target = self._candidate(name)
            target.write_text(bound_text, encoding="utf-8")
            digest = sha256_file(target)
            return ToolResult(
                True,
                "lean_draft",
                output=f"Formal candidate written: {target.name}",
                metadata={
                    "file": target.name,
                    "lean_sha256": digest,
                    "formal_draft": True,
                    "source_clean": True,
                    "theorem_name": theorem_name.strip(),
                    "theorem_type": self._compact(theorem_type),
                    "item_id": item_id,
                    "iteration": int(iteration),
                    "claim_hash": binding_hash,
                    "claim_sha256": claim_sha256,
                },
            )
        except Exception as exc:
            return ToolResult(False, "lean_draft", error=str(exc), metadata={"formal_verified": False})

    def _command_for(self, candidate: Path) -> tuple[list[str], str]:
        lake = shutil.which("lake")
        lean = shutil.which("lean")
        if lake:
            return [lake, "env", "lean", "-DwarningAsError=true", str(candidate)], "lake env lean"
        if lean:
            return [lean, "-DwarningAsError=true", str(candidate)], "lean"
        raise RuntimeError("lean/lake binary PATH üzerinde bulunamadı")

    def _run_lean(self, candidate: Path) -> tuple[subprocess.CompletedProcess[str], str]:
        command, checker = self._command_for(candidate)
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
        return proc, checker

    @staticmethod
    def _compiler_mentions_unsafe_proof(text: str) -> bool:
        lowered = text.lower()
        if "declaration uses 'sorry'" in lowered or 'declaration uses "sorry"' in lowered:
            return True
        return bool(re.search(r"\baxiom\b", lowered))

    def _axioms_ok(self, source: str, theorem_name: str) -> tuple[bool, str, list[str]]:
        audit = self.candidates / f".axioms-{uuid.uuid4().hex}.lean"
        try:
            audit.write_text(source.rstrip() + f"\n\n#print axioms {theorem_name}\n", encoding="utf-8")
            proc, _ = self._run_lean(audit)
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode != 0:
                return False, combined.strip() or "#print axioms failed", []
            lowered = combined.lower()
            if "does not depend on any axioms" in lowered:
                return True, combined.strip(), []
            match = re.search(r"axioms\s*:\s*\[([^\]]*)\]", combined, flags=re.I | re.S)
            if not match:
                return False, "#print axioms output could not be parsed", []
            found = [x.strip() for x in match.group(1).split(",") if x.strip()]
            allowed = {
                x.strip()
                for x in os.environ.get(
                    "LAB_LEAN_ALLOWED_AXIOMS",
                    "propext,Classical.choice,Quot.sound",
                ).split(",")
                if x.strip()
            }
            unexpected = [x for x in found if x not in allowed]
            if unexpected:
                return False, f"Unexpected Lean axioms: {unexpected}", found
            return True, combined.strip(), found
        finally:
            try:
                audit.unlink(missing_ok=True)
            except OSError:
                pass

    def check_file(
        self,
        name: str,
        *,
        expected_sha256: str = "",
        expected_item_id: str = "",
        expected_iteration: int | None = None,
        expected_claim_hash: str = "",
        expected_claim_sha256: str = "",
        expected_theorem_name: str = "",
        expected_theorem_type: str = "",
    ) -> ToolResult:
        metadata: dict[str, Any] = {
            "formal_verified": False,
            "source_clean": False,
            "axioms_verified": False,
            "formal_binding_verified": False,
            "claim_hash": "",
        }
        try:
            candidate = self._candidate(name)
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            source = candidate.read_text(encoding="utf-8")
            actual_sha = sha256_file(candidate)
            marker = self.CLAIM_MARKER.search(source)
            actual_claim_hash = marker.group(1).lower() if marker else ""
            expected_binding_hash = str(expected_claim_hash or expected_claim_sha256).strip().lower()
            metadata.update(
                {
                    "file": candidate.name,
                    "lean_sha256": actual_sha,
                    "theorem_name": expected_theorem_name,
                    "theorem_type": self._compact(expected_theorem_type),
                    "item_id": expected_item_id,
                    "iteration": expected_iteration,
                    "claim_hash": actual_claim_hash,
                    "claim_sha256": expected_claim_sha256,
                }
            )
            ok_source, reason = self._guard_source(source, expected_theorem_name, expected_theorem_type)
            if not ok_source:
                raise ValueError(reason)
            metadata["source_clean"] = True
            if not expected_sha256 or actual_sha != expected_sha256:
                raise ValueError("Lean source SHA-256 draft binding ile eşleşmiyor.")
            if not expected_item_id or expected_iteration is None or not expected_binding_hash:
                raise ValueError("Lean check ledger item/iteration/claim binding olmadan kabul edilemez.")
            if actual_claim_hash != expected_binding_hash:
                raise ValueError("Lean ailab-claim marker current ledger claim hash ile eşleşmiyor.")
            metadata["formal_binding_verified"] = True
            if os.environ.get("LAB_ALLOW_HOST_LEAN", "0") != "1":
                return ToolResult(
                    False,
                    "lean",
                    error="Generated Lean host execution is disabled. Set LAB_ALLOW_HOST_LEAN=1 only in a trusted/containerized Lean environment.",
                    metadata=metadata,
                )
            proc, checker = self._run_lean(candidate)
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            metadata.update({"returncode": proc.returncode, "checker": checker})
            if proc.returncode != 0:
                return ToolResult(False, "lean", output=proc.stdout.strip(), error=proc.stderr.strip(), metadata=metadata)
            if self._compiler_mentions_unsafe_proof(combined):
                return ToolResult(False, "lean", error="Lean compiler output mentions sorry/axiom; formal verification rejected.", metadata=metadata)
            axioms_ok, axioms_output, axioms = self._axioms_ok(source, expected_theorem_name)
            metadata["axioms"] = axioms
            metadata["axioms_verified"] = axioms_ok
            if not axioms_ok:
                return ToolResult(False, "lean", error=f"Axiom audit failed: {axioms_output}", metadata=metadata)
            metadata["formal_verified"] = True
            return ToolResult(True, "lean", output=proc.stdout.strip(), error=proc.stderr.strip(), metadata=metadata)
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, "lean", error=f"timeout ({self.timeout_s}s): {exc}", metadata=metadata)
        except Exception as exc:
            return ToolResult(False, "lean", error=str(exc), metadata=metadata)


class TropicalGridTool:
    """Finite-grid functional equality checker for nonnegative tropical weights.

    This tool intentionally does not compare symbolic monomial/provenance
    structure. Passing it means only that the candidate circuit agrees with the
    reference shortest-path function on the tested finite weight grid.

    It remains in the core temporarily for backward compatibility; the first
    Tropical problem pack will move the domain-specific checker out of ``lab``.
    """

    WARNING = (
        "Finite-grid functional equality only; this is not a formal "
        "monomial-level provenance equality proof."
    )

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
                if e not in weights:
                    raise ValueError(f"{gid}: invalid edge {e}")
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
            gates = circuit.get("gates", [])
            if not isinstance(gates, list) or not gates:
                raise ValueError("circuit.gates boş olmayan liste olmalı")
            edges = self._edges(n)
            edge_gate_count = sum(
                1 for gate in gates if str(gate.get("op", "")).lower() == "edge"
            )
            gate_count = sum(
                1 for gate in gates if str(gate.get("op", "")).lower() != "edge"
            )
            size_meta = {
                "gate_count": gate_count,
                "edge_gate_count": edge_gate_count,
                "total_gate_count": len(gates),
                "functional_equality_only": True,
                "warning": self.WARNING,
            }

            domain = sorted(set(int(x) for x in (weight_values or [0, 1, 2])))
            if not domain or any(x < 0 for x in domain):
                raise ValueError("weight_values nonnegative tamsayı olmalı")
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
                        **size_meta,
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
                **size_meta,
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
        problem_pack_root: str | Path | None = "problem_packs",
    ):
        secondary_roots = [problem_pack_root] if problem_pack_root is not None else []
        self.scripts = ScriptTool(script_root, trusted_roots=secondary_roots)
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
            return self.scripts.run(
                str(request.get("name", "")),
                [str(x) for x in request.get("args", [])],
            )
        if tool == "z3":
            return self.z3.check(str(request.get("smt2", "")))
        if tool == "lean_draft":
            return self.lean.draft_source(
                str(request.get("file") or "candidate.lean"),
                str(request.get("source") or ""),
                theorem_name=str(request.get("theorem_name") or ""),
                theorem_type=str(request.get("theorem_type") or ""),
                item_id=str(request.get("item_id") or ""),
                iteration=int(request["iteration"]) if request.get("iteration") is not None else None,
                claim_hash=str(request.get("claim_hash") or ""),
                claim_sha256=str(request.get("claim_sha256") or ""),
            )
        if tool == "lean":
            return ToolResult(
                False,
                "lean",
                error="Direct lean check is disabled for LLM requests; use lean_draft so the engine can bind and check the exact current candidate.",
            )
        if tool == "tropical_grid":
            circuit = request.get("circuit")
            if not isinstance(circuit, dict):
                return ToolResult(False, "tropical_grid", error="circuit JSON object gerekli")
            domain = request.get("weights")
            weights = [int(x) for x in domain] if isinstance(domain, list) else None
            return self.tropical_grid.check(circuit, weights)
        return ToolResult(False, tool, error=f"Bilinmeyen tool: {tool}")
