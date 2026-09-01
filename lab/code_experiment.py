from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from lab.agent import Agent
from lab.theorem_lab import extract_json_object
from lab.tools import ToolResult
from lab.trace import Trace


ALLOWED_SUFFIXES = {".py", ".txt", ".md", ".json", ".csv"}
SAFE_IMPORT_ROOTS = {
    "array",
    "bisect",
    "collections",
    "copy",
    "csv",
    "dataclasses",
    "decimal",
    "fractions",
    "functools",
    "heapq",
    "itertools",
    "json",
    "math",
    "operator",
    "random",
    "re",
    "statistics",
    "typing",
}
OPTIONAL_SCIENTIFIC_IMPORTS = {"numpy", "sympy", "networkx"}
BLOCKED_CALLS = {
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
    "__import__",
}


class UnsafeExperimentCode(ValueError):
    pass


@dataclass
class WorkspaceActionResult:
    ok: bool
    action: str
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata or {},
        }


class GuardedExperimentWorkspace:
    """Project-local workspace for LLM-authored computational experiments.

    This is a guarded execution environment, not a cryptographic OS sandbox.
    It intentionally provides no shell action, strips secrets from the child
    environment, confines file-management actions to the workspace, validates
    generated Python with a conservative AST policy, and runs Python in isolated
    mode with a hard timeout.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        timeout_s: int = 60,
        max_file_bytes: int = 250_000,
        max_read_chars: int = 120_000,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(exist_ok=True)
        self.timeout_s = int(timeout_s)
        self.max_file_bytes = int(max_file_bytes)
        self.max_read_chars = int(max_read_chars)
        self._run_counter = 0

    def _resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        raw = str(relative or "").strip().replace("\\", "/")
        if not raw or raw.startswith("/") or ":" in raw.split("/", 1)[0]:
            raise ValueError("Workspace içinde göreli bir dosya yolu gerekli.")
        candidate = (self.root / raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Dosya yolu workspace dışına çıkamaz.") from exc
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"İzin verilen uzantılar: {sorted(ALLOWED_SUFFIXES)}")
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    @staticmethod
    def _validate_python(code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise UnsafeExperimentCode(f"Python syntax error: {exc}") from exc

        allowed_imports = SAFE_IMPORT_ROOTS | OPTIONAL_SCIENTIFIC_IMPORTS
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    root = name.split(".", 1)[0]
                    if root not in allowed_imports:
                        raise UnsafeExperimentCode(f"İzin verilmeyen import: {root}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in BLOCKED_CALLS:
                    raise UnsafeExperimentCode(f"İzin verilmeyen çağrı: {node.func.id}")
            if isinstance(node, ast.Attribute) and str(node.attr).startswith("__"):
                raise UnsafeExperimentCode("Dunder attribute erişimi deney workspace'inde kapalıdır.")
            if isinstance(node, ast.Name) and str(node.id).startswith("__"):
                raise UnsafeExperimentCode("Dunder isimler deney workspace'inde kapalıdır.")

    def write_file(self, path: str, content: str) -> WorkspaceActionResult:
        try:
            target = self._resolve(path)
            payload = str(content)
            if len(payload.encode("utf-8")) > self.max_file_bytes:
                raise ValueError(f"Dosya {self.max_file_bytes} byte limitini aşıyor.")
            if target.suffix.lower() == ".py":
                self._validate_python(payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            return WorkspaceActionResult(
                True,
                "write_file",
                output=f"Yazıldı: {target.relative_to(self.root)} ({len(payload)} karakter)",
                metadata={"path": str(target.relative_to(self.root)), "chars": len(payload)},
            )
        except Exception as exc:
            return WorkspaceActionResult(False, "write_file", error=str(exc))

    def patch_file(self, path: str, old: str, new: str) -> WorkspaceActionResult:
        try:
            target = self._resolve(path, must_exist=True)
            text = target.read_text(encoding="utf-8")
            needle = str(old)
            if not needle:
                raise ValueError("patch_file için old boş olamaz.")
            count = text.count(needle)
            if count != 1:
                raise ValueError(f"old metni dosyada tam 1 kez bulunmalı; bulunan={count}")
            updated = text.replace(needle, str(new), 1)
            if len(updated.encode("utf-8")) > self.max_file_bytes:
                raise ValueError(f"Dosya {self.max_file_bytes} byte limitini aşıyor.")
            if target.suffix.lower() == ".py":
                self._validate_python(updated)
            target.write_text(updated, encoding="utf-8")
            return WorkspaceActionResult(
                True,
                "patch_file",
                output=f"Patch uygulandı: {target.relative_to(self.root)}",
                metadata={"path": str(target.relative_to(self.root))},
            )
        except Exception as exc:
            return WorkspaceActionResult(False, "patch_file", error=str(exc))

    def read_file(self, path: str) -> WorkspaceActionResult:
        try:
            target = self._resolve(path, must_exist=True)
            text = target.read_text(encoding="utf-8")
            truncated = len(text) > self.max_read_chars
            shown = text[: self.max_read_chars]
            if truncated:
                shown += "\n...[read truncated; full file remains on disk]"
            return WorkspaceActionResult(
                True,
                "read_file",
                output=shown,
                metadata={"path": str(target.relative_to(self.root)), "chars": len(text), "truncated": truncated},
            )
        except Exception as exc:
            return WorkspaceActionResult(False, "read_file", error=str(exc))

    def list_files(self) -> WorkspaceActionResult:
        files = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                try:
                    rel = str(path.relative_to(self.root))
                except ValueError:
                    continue
                files.append({"path": rel, "bytes": path.stat().st_size})
                if len(files) >= 250:
                    break
        return WorkspaceActionResult(
            True,
            "list_files",
            output=json.dumps(files, ensure_ascii=False, indent=2),
            metadata={"count": len(files)},
        )

    def _safe_env(self) -> dict[str, str]:
        keep = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        env = {key: os.environ[key] for key in keep if os.environ.get(key)}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONHASHSEED"] = "0"
        return env

    def run_python(self, path: str, args: list[str] | None = None) -> WorkspaceActionResult:
        try:
            target = self._resolve(path, must_exist=True)
            if target.suffix.lower() != ".py":
                raise ValueError("run_python yalnızca .py dosyası çalıştırır.")
            code = target.read_text(encoding="utf-8")
            self._validate_python(code)
            clean_args = [str(x)[:500] for x in (args or [])][:20]
            started = time.monotonic()
            proc = subprocess.run(
                [sys.executable, "-I", str(target), *clean_args],
                cwd=str(self.root),
                env=self._safe_env(),
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
            elapsed = time.monotonic() - started
            self._run_counter += 1
            stem = f"run_{self._run_counter:04d}_{target.stem}"
            stdout_path = self.outputs / f"{stem}.stdout.txt"
            stderr_path = self.outputs / f"{stem}.stderr.txt"
            stdout_path.write_text(proc.stdout or "", encoding="utf-8")
            stderr_path.write_text(proc.stderr or "", encoding="utf-8")
            return WorkspaceActionResult(
                proc.returncode == 0,
                "run_python",
                output=(proc.stdout or "").strip(),
                error=(proc.stderr or "").strip(),
                metadata={
                    "path": str(target.relative_to(self.root)),
                    "args": clean_args,
                    "returncode": proc.returncode,
                    "wall_time_s": elapsed,
                    "stdout_file": str(stdout_path.relative_to(self.root)),
                    "stderr_file": str(stderr_path.relative_to(self.root)),
                    "evidence_level": "COMPUTATION_ONLY",
                },
            )
        except subprocess.TimeoutExpired as exc:
            return WorkspaceActionResult(
                False,
                "run_python",
                error=f"timeout ({self.timeout_s}s): {exc}",
                metadata={"evidence_level": "COMPUTATION_ONLY"},
            )
        except Exception as exc:
            return WorkspaceActionResult(False, "run_python", error=str(exc))

    def execute(self, action: dict[str, Any]) -> WorkspaceActionResult:
        name = str(action.get("action") or "").strip().lower()
        if name == "write_file":
            return self.write_file(str(action.get("path") or ""), str(action.get("content") or ""))
        if name == "patch_file":
            return self.patch_file(
                str(action.get("path") or ""),
                str(action.get("old") or ""),
                str(action.get("new") or ""),
            )
        if name == "read_file":
            return self.read_file(str(action.get("path") or ""))
        if name == "list_files":
            return self.list_files()
        if name == "run_python":
            args = action.get("args")
            return self.run_python(
                str(action.get("path") or ""),
                [str(x) for x in args] if isinstance(args, list) else None,
            )
        return WorkspaceActionResult(False, name or "unknown", error=f"Bilinmeyen action: {name}")


CODE_EXPERIMENT_SYSTEM_PROMPT = """Sen CodeExperimentAgent'sın.
Görevin matematik/teorik CS araştırmasındaki aday iddiaları hesaplamalı deneylerle sınamak.
Kendi proje workspace'in içinde dosya yazabilir, patch edebilir, okuyabilir, listeleyebilir ve Python çalıştırabilirsin.
Serbest shell yoktur. API anahtarlarına veya workspace dışına erişmeye çalışma.

Her tur SADECE tek bir JSON object döndür:
{
  "action": "write_file|patch_file|read_file|list_files|run_python|finish",
  "path": "exp_001.py",
  "content": "write_file için tam içerik",
  "old": "patch_file için birebir eski parça",
  "new": "patch_file için yeni parça",
  "args": [],
  "summary": "finish için kısa deney özeti"
}

İlk denemede küçük, deterministik ve denetlenebilir script yaz. Hata/karşıörnek görürsen çıktıya göre düzelt veya yeni deney tasarla.
Finite computation'ı ispat olarak sunma. finish yalnız deneyden öğrenilecek başka anlamlı şey kalmadığında kullan.
"""


class CodeExperimentRunner:
    def __init__(
        self,
        workspace: GuardedExperimentWorkspace,
        trace: Trace,
        *,
        max_steps: int = 8,
        observation_limit: int = 20_000,
    ):
        self.workspace = workspace
        self.trace = trace
        self.max_steps = max(1, int(max_steps))
        self.observation_limit = max(2_000, int(observation_limit))

    @staticmethod
    def _action_for_trace(action: dict[str, Any]) -> dict[str, Any]:
        safe = dict(action)
        content = safe.get("content")
        if isinstance(content, str) and len(content) > 80_000:
            safe["content"] = content[:80_000] + "\n...[trace truncated]"
        return safe

    def run(
        self,
        *,
        agent: Agent,
        task: str,
        step_key: str,
        call_agent: Callable[[Agent, str, str], str],
        execute_cached: Callable[[str, dict[str, Any]], WorkspaceActionResult],
    ) -> ToolResult:
        observation = self.workspace.list_files().output
        last_result: WorkspaceActionResult | None = None
        for turn in range(1, self.max_steps + 1):
            prompt = (
                f"EXPERIMENT TASK:\n{task}\n\n"
                f"WORKSPACE / PREVIOUS OBSERVATION:\n{observation[-self.observation_limit:]}\n\n"
                "Choose exactly one next action. Return only the JSON action object. "
                "Use finish only after you have actually run enough code to support the computational conclusion."
            )
            raw = call_agent(agent, prompt, f"{step_key}:plan:{turn}")
            action = extract_json_object(raw)
            action_name = str(action.get("action") or "").lower()
            self.trace.log(
                "code_experiment_action",
                step_key=step_key,
                turn=turn,
                agent=agent.name,
                model=agent.model,
                action=self._action_for_trace(action),
            )
            if action_name == "finish":
                summary = str(action.get("summary") or "Deney tamamlandı.")
                files = self.workspace.list_files()
                payload = {
                    "status": "EXPERIMENT_COMPLETE",
                    "evidence_level": "COMPUTATION_ONLY",
                    "summary": summary,
                    "turns": turn,
                    "workspace": str(self.workspace.root),
                    "files": json.loads(files.output) if files.ok else [],
                    "warning": "Computational evidence is not a proof.",
                }
                self.trace.log("code_experiment_complete", step_key=step_key, **payload)
                return ToolResult(True, "code_experiment", output=summary, metadata=payload)

            last_result = execute_cached(f"{step_key}:action:{turn}", action)
            self.trace.log(
                "code_experiment_result",
                step_key=step_key,
                turn=turn,
                action=action_name,
                **last_result.as_dict(),
            )
            observation = json.dumps(last_result.as_dict(), ensure_ascii=False, indent=2)

        return ToolResult(
            False,
            "code_experiment",
            output=(last_result.output if last_result else ""),
            error=f"CodeExperimentAgent {self.max_steps} action limitine ulaştı; finish üretmedi.",
            metadata={
                "status": "STEP_LIMIT",
                "evidence_level": "COMPUTATION_ONLY",
                "workspace": str(self.workspace.root),
            },
        )
