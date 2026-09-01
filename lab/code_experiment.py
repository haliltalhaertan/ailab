from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

from lab.agent import Agent
from lab.integrity import sha256_file
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
    """Project-local execution boundary for LLM-authored experiments.

    The generated program gets no shell action and is AST-filtered before launch.
    Runtime execution additionally uses an isolated Python process, a scrubbed
    environment, wall-time cancellation, process-tree monitoring, memory/PID
    limits, and bounded on-disk stdout/stderr. This materially reduces local risk,
    but it is still not a VM/container security boundary or a network namespace.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        timeout_s: int = 60,
        max_file_bytes: int = 250_000,
        max_read_chars: int = 120_000,
        max_output_bytes: int = 4 * 1024 * 1024,
        memory_limit_mb: int = 768,
        pid_limit: int = 8,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(exist_ok=True)
        self.timeout_s = int(timeout_s)
        self.max_file_bytes = int(max_file_bytes)
        self.max_read_chars = int(max_read_chars)
        self.max_output_bytes = max(64 * 1024, int(max_output_bytes))
        self.memory_limit_bytes = max(128, int(memory_limit_mb)) * 1024 * 1024
        self.pid_limit = max(1, int(pid_limit))
        self.cancel_check = cancel_check
        self.available_optional_imports = {
            name for name in OPTIONAL_SCIENTIFIC_IMPORTS if importlib.util.find_spec(name) is not None
        }

    def capability_summary(self) -> str:
        optional = ", ".join(sorted(self.available_optional_imports)) or "(none installed)"
        return (
            f"stdlib imports: {', '.join(sorted(SAFE_IMPORT_ROOTS))}; "
            f"optional scientific imports currently installed: {optional}. "
            "Do not import an optional package unless it appears in this list."
        )

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

    def _validate_python(self, code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise UnsafeExperimentCode(f"Python syntax error: {exc}") from exc

        allowed_imports = SAFE_IMPORT_ROOTS | self.available_optional_imports
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    root = name.split(".", 1)[0]
                    if root not in allowed_imports:
                        if root in OPTIONAL_SCIENTIFIC_IMPORTS:
                            raise UnsafeExperimentCode(
                                f"Opsiyonel paket kurulu değil: {root}. Capability listesine göre script üret."
                            )
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
                metadata={
                    "path": str(target.relative_to(self.root)),
                    "chars": len(payload),
                    "sha256": sha256_file(target),
                },
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
                metadata={"path": str(target.relative_to(self.root)), "sha256": sha256_file(target)},
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
                metadata={
                    "path": str(target.relative_to(self.root)),
                    "chars": len(text),
                    "truncated": truncated,
                    "sha256": sha256_file(target),
                },
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

    def _evidence_paths(self, target: Path) -> tuple[str, Path, Path]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        evidence_id = f"{stamp}_{uuid.uuid4().hex[:10]}_{target.stem}"
        return (
            evidence_id,
            self.outputs / f"{evidence_id}.stdout.txt",
            self.outputs / f"{evidence_id}.stderr.txt",
        )

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            try:
                parent.kill()
            except psutil.Error:
                pass
        except psutil.Error:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass

    @staticmethod
    def _tree_stats(pid: int) -> tuple[int, int]:
        try:
            parent = psutil.Process(pid)
            processes = [parent] + parent.children(recursive=True)
        except psutil.Error:
            return 0, 0
        rss = 0
        alive = 0
        for process in processes:
            try:
                rss += int(process.memory_info().rss)
                alive += 1
            except psutil.Error:
                continue
        return rss, alive

    @staticmethod
    def _preview(path: Path, limit: int = 20_000) -> str:
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        if len(raw) > limit:
            raw = raw[-limit:]
            prefix = "...[output preview truncated to tail]...\n"
        else:
            prefix = ""
        return prefix + raw.decode("utf-8", errors="replace")

    def run_python(self, path: str, args: list[str] | None = None) -> WorkspaceActionResult:
        target: Path | None = None
        stdout_path: Path | None = None
        stderr_path: Path | None = None
        proc: subprocess.Popen | None = None
        try:
            target = self._resolve(path, must_exist=True)
            if target.suffix.lower() != ".py":
                raise ValueError("run_python yalnızca .py dosyası çalıştırır.")
            code = target.read_text(encoding="utf-8")
            self._validate_python(code)
            clean_args = [str(x)[:500] for x in (args or [])][:20]
            evidence_id, stdout_path, stderr_path = self._evidence_paths(target)
            started = time.monotonic()
            creationflags = 0
            start_new_session = os.name != "nt"
            if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                proc = subprocess.Popen(
                    [sys.executable, "-I", str(target), *clean_args],
                    cwd=str(self.root),
                    env=self._safe_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    creationflags=creationflags,
                    start_new_session=start_new_session,
                )
                termination_reason = ""
                peak_rss = 0
                peak_pids = 1
                while proc.poll() is None:
                    elapsed = time.monotonic() - started
                    rss, pids = self._tree_stats(proc.pid)
                    peak_rss = max(peak_rss, rss)
                    peak_pids = max(peak_pids, pids)
                    output_bytes = stdout_path.stat().st_size + stderr_path.stat().st_size
                    if self.cancel_check and self.cancel_check():
                        termination_reason = "cancelled"
                    elif elapsed > self.timeout_s:
                        termination_reason = "timeout"
                    elif rss > self.memory_limit_bytes:
                        termination_reason = "memory_limit"
                    elif pids > self.pid_limit:
                        termination_reason = "pid_limit"
                    elif output_bytes > self.max_output_bytes:
                        termination_reason = "output_limit"
                    if termination_reason:
                        self._kill_tree(proc)
                        break
                    time.sleep(0.05)
                try:
                    returncode = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._kill_tree(proc)
                    returncode = proc.wait(timeout=2)
            elapsed = time.monotonic() - started
            stdout_preview = self._preview(stdout_path)
            stderr_preview = self._preview(stderr_path)
            ok = returncode == 0 and not termination_reason
            metadata = {
                "path": str(target.relative_to(self.root)),
                "args": clean_args,
                "returncode": returncode,
                "wall_time_s": elapsed,
                "termination_reason": termination_reason,
                "peak_rss_bytes": peak_rss,
                "peak_processes": peak_pids,
                "memory_limit_bytes": self.memory_limit_bytes,
                "pid_limit": self.pid_limit,
                "max_output_bytes": self.max_output_bytes,
                "stdout_file": str(stdout_path.relative_to(self.root)),
                "stderr_file": str(stderr_path.relative_to(self.root)),
                "script_sha256": sha256_file(target),
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_sha256": sha256_file(stderr_path),
                "evidence_id": evidence_id,
                "evidence_level": "COMPUTATION_ONLY",
            }
            error = stderr_preview.strip()
            if termination_reason:
                suffix = {
                    "cancelled": "kullanıcı durdurma isteği",
                    "timeout": f"timeout ({self.timeout_s}s)",
                    "memory_limit": "memory limiti aşıldı",
                    "pid_limit": "process limiti aşıldı",
                    "output_limit": "stdout/stderr limiti aşıldı",
                }.get(termination_reason, termination_reason)
                error = (error + "\n" if error else "") + suffix
            return WorkspaceActionResult(
                ok,
                "run_python",
                output=stdout_preview.strip(),
                error=error,
                metadata=metadata,
            )
        except Exception as exc:
            if proc is not None and proc.poll() is None:
                self._kill_tree(proc)
            metadata: dict[str, Any] = {"evidence_level": "COMPUTATION_ONLY"}
            if target is not None and target.exists():
                metadata["path"] = str(target.relative_to(self.root))
                metadata["script_sha256"] = sha256_file(target)
            if stdout_path is not None and stdout_path.exists():
                metadata["stdout_file"] = str(stdout_path.relative_to(self.root))
                metadata["stdout_sha256"] = sha256_file(stdout_path)
            if stderr_path is not None and stderr_path.exists():
                metadata["stderr_file"] = str(stderr_path.relative_to(self.root))
                metadata["stderr_sha256"] = sha256_file(stderr_path)
            return WorkspaceActionResult(False, "run_python", error=str(exc), metadata=metadata)

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
finish ancak en az bir gerçek run_python başarıyla bittikten ve en son run_python denemesi başarılı olduktan sonra kabul edilir.
Finite computation'ı ispat olarak sunma.
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
        successful_runs: list[dict[str, Any]] = []
        failed_runs: list[dict[str, Any]] = []
        last_run_ok: bool | None = None
        last_result: WorkspaceActionResult | None = None
        for turn in range(1, self.max_steps + 1):
            prompt = (
                f"EXPERIMENT TASK:\n{task}\n\n"
                f"RUNTIME CAPABILITIES:\n{self.workspace.capability_summary()}\n\n"
                f"WORKSPACE / PREVIOUS OBSERVATION:\n{observation[-self.observation_limit:]}\n\n"
                f"Successful Python runs so far: {len(successful_runs)}; failed runs: {len(failed_runs)}.\n"
                "Choose exactly one next action. Return only the JSON action object."
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
                if not successful_runs or last_run_ok is not True:
                    reason = (
                        "finish reddedildi: en az bir başarılı run_python gerekli ve en son run_python "
                        "denemesi başarılı olmalı. Gerçek deney çalıştır veya son hatayı düzelt."
                    )
                    self.trace.log(
                        "code_experiment_finish_rejected",
                        step_key=step_key,
                        turn=turn,
                        successful_runs=len(successful_runs),
                        failed_runs=len(failed_runs),
                        last_run_ok=last_run_ok,
                        reason=reason,
                    )
                    observation = reason
                    continue
                summary = str(action.get("summary") or "Deney tamamlandı.")
                files = self.workspace.list_files()
                evidence = {
                    "successful_runs": successful_runs,
                    "failed_runs": failed_runs,
                    "successful_run_count": len(successful_runs),
                    "failed_run_count": len(failed_runs),
                }
                payload = {
                    "status": "EXPERIMENT_COMPLETE",
                    "evidence_level": "COMPUTATION_ONLY",
                    "summary": summary,
                    "turns": turn,
                    "workspace": str(self.workspace.root),
                    "files": json.loads(files.output) if files.ok else [],
                    "evidence": evidence,
                    "warning": "Computational evidence is not a proof.",
                }
                self.trace.log("code_experiment_complete", step_key=step_key, **payload)
                return ToolResult(True, "code_experiment", output=summary, metadata=payload)

            last_result = execute_cached(f"{step_key}:action:{turn}", action)
            result_payload = last_result.as_dict()
            self.trace.log(
                "code_experiment_result",
                step_key=step_key,
                turn=turn,
                executed_action=action_name,
                result=result_payload,
            )
            if action_name == "run_python":
                evidence_record = {
                    "ok": bool(last_result.ok),
                    **dict(last_result.metadata or {}),
                }
                last_run_ok = bool(last_result.ok)
                if last_result.ok:
                    successful_runs.append(evidence_record)
                else:
                    failed_runs.append(evidence_record)
            observation = json.dumps(result_payload, ensure_ascii=False, indent=2)

        return ToolResult(
            False,
            "code_experiment",
            output=(last_result.output if last_result else ""),
            error=f"CodeExperimentAgent {self.max_steps} action limitine ulaştı; geçerli finish üretmedi.",
            metadata={
                "status": "STEP_LIMIT",
                "evidence_level": "COMPUTATION_ONLY",
                "workspace": str(self.workspace.root),
                "successful_run_count": len(successful_runs),
                "failed_run_count": len(failed_runs),
            },
        )
