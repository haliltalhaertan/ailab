from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lab.agent import Agent
from lab.integrity import sha256_file
from lab.json_io import StructuredOutputError, parse_json_object
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
BLOCKED_NAMES = {
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
BLOCKED_ATTRIBUTES = {
    "sys",
    "os",
    "subprocess",
    "builtins",
    "importlib",
    "ctypes",
    "socket",
    "modules",
    "format_map",
}
BLOCKED_OPERATOR_CALLS = {"attrgetter", "methodcaller"}

INFRASTRUCTURE_ERROR_MARKERS = (
    "failed to connect to the docker api",
    "cannot connect to the docker daemon",
    "error during connect",
    "is the docker daemon running",
    "daemon is running?",
    "pull access denied",
    "unable to find image",
    "no such image",
    "error response from daemon",
    "cannot connect to podman",
    "error pulling image",
    "manifest unknown",
)


def infrastructure_failure(result: Any) -> bool:
    metadata = getattr(result, "metadata", None)
    if isinstance(result, dict):
        metadata = result.get("metadata")
        error = str(result.get("error") or "")
    else:
        error = str(getattr(result, "error", "") or "")
    if isinstance(metadata, dict) and metadata.get("infrastructure_error") is True:
        return True
    folded = error.casefold()
    return any(marker in folded for marker in INFRASTRUCTURE_ERROR_MARKERS)


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
    """Project-local authoring workspace with container-only execution.

    The AST policy is best-effort defense in depth and may be bypassable; it is
    not the security boundary. Generated Python is *never* executed directly on
    the host. The only execution security boundary is the disposable Docker or
    Podman container used by ``run_python``. No container means fail closed.
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
        cpu_limit: float = 1.0,
        cancel_check: Callable[[], bool] | None = None,
        container_engine: str | None = None,
        container_image: str | None = None,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(exist_ok=True)
        # Rootless/user-namespaced Docker may map container root to an unrelated
        # host uid. Outputs are intentionally the container-writable evidence
        # area, so make that one directory world-writable on POSIX. The rest of
        # the workspace keeps normal host permissions.
        if os.name != "nt":
            try:
                self.outputs.chmod(0o777)
            except OSError:
                pass
        self.timeout_s = int(timeout_s)
        self.max_file_bytes = int(max_file_bytes)
        self.max_read_chars = int(max_read_chars)
        self.max_output_bytes = max(64 * 1024, int(max_output_bytes))
        self.memory_limit_mb = max(128, int(memory_limit_mb))
        self.pid_limit = max(1, int(pid_limit))
        self.cpu_limit = max(0.1, float(cpu_limit))
        self.cancel_check = cancel_check
        requested = str(container_engine or os.environ.get("LAB_CODE_CONTAINER_ENGINE") or "").strip()
        self.container_engine = requested or self._discover_engine()
        self.container_image = str(
            container_image or os.environ.get("LAB_CODE_CONTAINER_IMAGE") or "python:3.12-slim"
        ).strip()
        extras = str(os.environ.get("LAB_CODE_EXTRA_IMPORTS") or "").strip()
        self.extra_imports = {x.strip() for x in extras.split(",") if x.strip()}
        self._execution_available = False
        self._availability_reason = "container engine henüz doğrulanmadı"
        self._daemon_version = ""
        self.refresh_execution_availability()

    @staticmethod
    def _discover_engine() -> str:
        for name in ("docker", "podman"):
            if shutil.which(name):
                return name
        return ""

    def _engine_cli_available(self) -> bool:
        return bool(self.container_engine and shutil.which(self.container_engine))

    @staticmethod
    def _daemon_version_from_info(text: str) -> str:
        for pattern in (r"Server Version:\s*([^\s]+)", r"version:\s*([^\s]+)"):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def refresh_execution_availability(self) -> bool:
        if not self._engine_cli_available():
            self._execution_available = False
            self._daemon_version = ""
            self._availability_reason = "container CLI bulunamadı"
            return False
        try:
            probe = subprocess.run(
                [self.container_engine, "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception as exc:
            self._execution_available = False
            self._daemon_version = ""
            self._availability_reason = f"daemon erişilemiyor: {exc}"
            return False
        output = (str(probe.stdout or "") + "\n" + str(probe.stderr or "")).strip()
        if probe.returncode != 0:
            detail = output[-500:] or f"{self.container_engine} info exit={probe.returncode}"
            self._execution_available = False
            self._daemon_version = ""
            self._availability_reason = f"daemon erişilemiyor: {detail}"
            return False
        self._daemon_version = self._daemon_version_from_info(output)
        suffix = f": {self._daemon_version}" if self._daemon_version else f": {self.container_engine}"
        self._execution_available = True
        self._availability_reason = "daemon çalışıyor" + suffix
        return True

    @property
    def execution_available(self) -> bool:
        return bool(self._execution_available)

    @property
    def availability_reason(self) -> str:
        return self._availability_reason

    @property
    def daemon_version(self) -> str:
        return self._daemon_version

    def capability_summary(self) -> str:
        engine = self.container_engine or "NONE"
        extras = ", ".join(sorted(self.extra_imports)) or "none"
        return (
            f"execution=disposable-container; engine={engine}; image={self.container_image}; network=none; "
            f"rootfs=read-only; workspace-only writable mount; memory={self.memory_limit_mb}MB; "
            f"pids={self.pid_limit}; cpus={self.cpu_limit}; timeout={self.timeout_s}s; "
            f"stdlib imports={', '.join(sorted(SAFE_IMPORT_ROOTS))}; configured extra imports={extras}. "
            + ("Container execution is available; " if self.execution_available else "Container execution is NOT available; ")
            + self.availability_reason
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
        """Reject known dangerous AST patterns; container remains the boundary."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise UnsafeExperimentCode(f"Python syntax error: {exc}") from exc
        allowed_imports = SAFE_IMPORT_ROOTS | self.extra_imports
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    root = name.split(".", 1)[0]
                    if root not in allowed_imports:
                        raise UnsafeExperimentCode(f"İzin verilmeyen import: {root}")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in BLOCKED_NAMES:
                raise UnsafeExperimentCode(f"İzin verilmeyen isim erişimi: {node.id}")
            if isinstance(node, ast.Name) and str(node.id).startswith("__"):
                raise UnsafeExperimentCode("Dunder isimler kapalıdır.")
            if isinstance(node, ast.Attribute):
                attr = str(node.attr)
                if attr.startswith("_"):
                    raise UnsafeExperimentCode(f"Private/dunder attribute erişimi kapalıdır: {attr}")
                if attr in BLOCKED_ATTRIBUTES:
                    raise UnsafeExperimentCode(f"İzin verilmeyen attribute erişimi: {attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
                raise UnsafeExperimentCode("String sabitlerinde dunder erişim kalıpları kapalıdır.")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    if func.attr in BLOCKED_OPERATOR_CALLS:
                        raise UnsafeExperimentCode(f"İzin verilmeyen operator helper çağrısı: {func.attr}")
                elif isinstance(func, ast.Name) and func.id in BLOCKED_OPERATOR_CALLS:
                    raise UnsafeExperimentCode(f"İzin verilmeyen operator helper çağrısı: {func.id}")

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
                metadata={"path": str(target.relative_to(self.root)), "chars": len(payload), "sha256": sha256_file(target)},
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
            return WorkspaceActionResult(True, "patch_file", output=f"Patch uygulandı: {target.relative_to(self.root)}", metadata={"path": str(target.relative_to(self.root)), "sha256": sha256_file(target)})
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
            return WorkspaceActionResult(True, "read_file", output=shown, metadata={"path": str(target.relative_to(self.root)), "chars": len(text), "truncated": truncated, "sha256": sha256_file(target)})
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
        return WorkspaceActionResult(True, "list_files", output=json.dumps(files, ensure_ascii=False, indent=2), metadata={"count": len(files)})

    def _evidence_paths(self, target: Path) -> tuple[str, Path, Path]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        evidence_id = f"{stamp}_{uuid.uuid4().hex[:10]}_{target.stem}"
        return evidence_id, self.outputs / f"{evidence_id}.stdout.txt", self.outputs / f"{evidence_id}.stderr.txt"

    @staticmethod
    def _preview(path: Path, limit: int = 20_000) -> str:
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        prefix = ""
        if len(raw) > limit:
            raw = raw[-limit:]
            prefix = "...[output preview truncated to tail]...\n"
        return prefix + raw.decode("utf-8", errors="replace")

    def _container_name(self) -> str:
        return "ailab-exp-" + uuid.uuid4().hex[:16]

    def _kill_container(self, name: str) -> None:
        if not self._engine_cli_available():
            return
        try:
            subprocess.run(
                [self.container_engine, "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            pass

    def run_python(self, path: str, args: list[str] | None = None) -> WorkspaceActionResult:
        target: Path | None = None
        stdout_path: Path | None = None
        stderr_path: Path | None = None
        proc: subprocess.Popen | None = None
        container_name = self._container_name()
        termination_reason = ""
        container_spawn_attempted = False
        try:
            target = self._resolve(path, must_exist=True)
            if target.suffix.lower() != ".py":
                raise ValueError("run_python yalnızca .py dosyası çalıştırır.")
            code = target.read_text(encoding="utf-8")
            self._validate_python(code)
            if not self.refresh_execution_availability():
                return WorkspaceActionResult(
                    False,
                    "run_python",
                    error=f"infrastructure: {self.availability_reason}",
                    metadata={
                        "evidence_level": "COMPUTATION_ONLY",
                        "container_required": True,
                        "infrastructure_error": True,
                        "tool_unavailable": True,
                        "availability_reason": self.availability_reason,
                        "container_engine": self.container_engine,
                    },
                )
            clean_args = [str(x)[:500] for x in (args or [])][:20]
            evidence_id, stdout_path, stderr_path = self._evidence_paths(target)
            rel_script = target.relative_to(self.root).as_posix()
            mount = f"type=bind,source={self.root},target=/workspace"
            command = [
                self.container_engine,
                "run",
                "--rm",
                "--name",
                container_name,
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                f"--memory={self.memory_limit_mb}m",
                f"--pids-limit={self.pid_limit}",
                f"--cpus={self.cpu_limit}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                mount,
                "--workdir",
                "/workspace",
                self.container_image,
                "python",
                "-I",
                f"/workspace/{rel_script}",
                *clean_args,
            ]
            started = time.monotonic()
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                container_spawn_attempted = True
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                while proc.poll() is None:
                    elapsed = time.monotonic() - started
                    output_bytes = stdout_path.stat().st_size + stderr_path.stat().st_size
                    if self.cancel_check and self.cancel_check():
                        termination_reason = "cancelled"
                    elif elapsed > self.timeout_s:
                        termination_reason = "timeout"
                    elif output_bytes > self.max_output_bytes:
                        termination_reason = "output_limit"
                    if termination_reason:
                        self._kill_container(container_name)
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    time.sleep(0.05)
                try:
                    returncode = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill_container(container_name)
                    proc.kill()
                    returncode = proc.wait(timeout=2)
            elapsed = time.monotonic() - started
            output_bytes = stdout_path.stat().st_size + stderr_path.stat().st_size
            if output_bytes > self.max_output_bytes and not termination_reason:
                termination_reason = "output_limit"
            stdout_preview = self._preview(stdout_path)
            stderr_preview = self._preview(stderr_path)
            ok = returncode == 0 and not termination_reason
            metadata = {
                "path": str(target.relative_to(self.root)),
                "args": clean_args,
                "returncode": returncode,
                "wall_time_s": elapsed,
                "termination_reason": termination_reason,
                "max_output_bytes": self.max_output_bytes,
                "container_engine": self.container_engine,
                "container_image": self.container_image,
                "network": "none",
                "rootfs": "read-only",
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
                reason = {
                    "cancelled": "kullanıcı durdurma isteği",
                    "timeout": f"timeout ({self.timeout_s}s)",
                    "output_limit": "stdout/stderr limiti aşıldı",
                }.get(termination_reason, termination_reason)
                error = chr(10).join(part for part in (error, reason) if part)
            result = WorkspaceActionResult(ok, "run_python", output=stdout_preview.strip(), error=error, metadata=metadata)
            if not ok and not termination_reason and infrastructure_failure(result):
                metadata.update(
                    {
                        "infrastructure_error": True,
                        "tool_unavailable": True,
                        "availability_reason": error or "container runtime failure",
                    }
                )
                return WorkspaceActionResult(
                    False,
                    "run_python",
                    output=stdout_preview.strip(),
                    error="infrastructure: " + (error or "container runtime failure"),
                    metadata=metadata,
                )
            return result
        except Exception as exc:
            if proc is not None and proc.poll() is None:
                self._kill_container(container_name)
                try:
                    proc.kill()
                except Exception:
                    pass
            metadata: dict[str, Any] = {"evidence_level": "COMPUTATION_ONLY", "container_required": True}
            if container_spawn_attempted and isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
                metadata.update(
                    {
                        "infrastructure_error": True,
                        "tool_unavailable": True,
                        "availability_reason": str(exc),
                    }
                )
            if target is not None and target.exists():
                metadata["path"] = str(target.relative_to(self.root))
                metadata["script_sha256"] = sha256_file(target)
            if stdout_path is not None and stdout_path.exists():
                metadata["stdout_file"] = str(stdout_path.relative_to(self.root))
                metadata["stdout_sha256"] = sha256_file(stdout_path)
            if stderr_path is not None and stderr_path.exists():
                metadata["stderr_file"] = str(stderr_path.relative_to(self.root))
                metadata["stderr_sha256"] = sha256_file(stderr_path)
            prefix = "infrastructure: " if metadata.get("infrastructure_error") else ""
            return WorkspaceActionResult(False, "run_python", error=prefix + str(exc), metadata=metadata)
        finally:
            self._kill_container(container_name)

    def execute(self, action: dict[str, Any]) -> WorkspaceActionResult:
        name = str(action.get("action") or "").strip().lower()
        if name == "write_file":
            return self.write_file(str(action.get("path") or ""), str(action.get("content") or ""))
        if name == "patch_file":
            return self.patch_file(str(action.get("path") or ""), str(action.get("old") or ""), str(action.get("new") or ""))
        if name == "read_file":
            return self.read_file(str(action.get("path") or ""))
        if name == "list_files":
            return self.list_files()
        if name == "run_python":
            raw_args = action.get("args")
            args = [str(x) for x in raw_args] if isinstance(raw_args, list) else []
            run_path = str(action.get("path") or "").strip()
            normalized_from_args = False
            if not run_path and args:
                candidate = args[0].strip()
                candidate_path = Path(candidate)
                if (
                    candidate
                    and not candidate_path.is_absolute()
                    and ".." not in candidate_path.parts
                    and candidate_path.suffix.lower() == ".py"
                ):
                    run_path = candidate
                    args = args[1:]
                    normalized_from_args = True
            if not run_path:
                return WorkspaceActionResult(
                    False,
                    "run_python",
                    error=(
                        'run_python için "path" alanı gerekli; script dosyasını args içine koymayın. '
                        'Örnek: {"action":"run_python","path":"exp_001.py","args":[]}'
                    ),
                )
            result = self.run_python(run_path, args)
            if normalized_from_args:
                result.metadata = {**(result.metadata or {}), "normalized_path_from_args": True}
            return result
        return WorkspaceActionResult(False, name or "unknown", error=f"Bilinmeyen action: {name}")


CODE_EXPERIMENT_SYSTEM_PROMPT = """Sen CodeExperimentAgent'sın.
Sen uygulayıcısın. Theorist'in belirttiği hesabı/deneyi kodla; matematiksel analiz, ispat denemesi veya yeni teori geliştirme yapma.
Reasoning'i kısa tut. İlk eylemin deney scriptini write_file ile yazmak olsun; sonra çalıştır, sonucu gözle ve gerekirse küçük düzeltme yap.
Görevin matematik/teorik CS araştırmasındaki aday iddiaları hesaplamalı deneylerle sınamak.
Dosya işlemleri proje workspace'iyle sınırlıdır. Python yalnız no-network disposable container içinde çalışır.
Host shell, host filesystem ve API anahtarları erişilebilir değildir.

ÖNEMLİ ACTION ŞEMASI:
- write_file: {"action":"write_file","path":"exp_001.py","content":"..."}
- run_python: {"action":"run_python","path":"exp_001.py","args":[]}
- `run_python.args` yalnız script'e verilecek komut satırı argümanlarıdır; script dosya adını `args` içine koyma, `path` alanına koy.
- read_file/patch_file/list_files aynı workspace içindeki göreli yolları kullanır.

PYTHON POLİTİKASI — kodu yazmadan önce uygula:
- Dunder isim/attribute kullanma: `__name__`, `__main__`, `__dict__` vb. yasaktır. Top-level kod doğrudan çalışabilir; `if __name__ == "__main__"` yazma.
- `_` ile başlayan private attribute erişimleri yasaktır.
- Yalnız izinli saf-kütüphane importlarını kullan; os/sys/subprocess/socket/importlib/ctypes ve ağ/host erişimi yoktur.
- open/eval/exec/getattr/setattr/__import__/globals/locals/vars gibi dinamik veya host-I/O built-in'leri kullanma.

Her tur SADECE tek JSON object döndür:
{
  "action": "write_file|patch_file|read_file|list_files|run_python|finish",
  "path": "exp_001.py",
  "content": "write_file için tam içerik",
  "old": "patch_file için birebir eski parça",
  "new": "patch_file için yeni parça",
  "args": [],
  "summary": "finish için kısa deney özeti"
}

Önce küçük, deterministik ve denetlenebilir script yaz. Her tur geçmiş aksiyon/sonuç özetini kullan; aynı hatayı körlemesine tekrarlama.
finish ancak en az bir gerçek run_python başarıyla bittikten ve en son run_python denemesi başarılı olduktan sonra kabul edilir.
Finite computation ispat değildir.
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
        if not self.workspace.refresh_execution_availability():
            return ToolResult(
                False,
                "code_experiment",
                error=f"infrastructure: {self.workspace.availability_reason}",
                metadata={
                    "status": "CONTAINER_UNAVAILABLE",
                    "evidence_level": "COMPUTATION_ONLY",
                    "infrastructure_error": True,
                    "tool_unavailable": True,
                    "availability_reason": self.workspace.availability_reason,
                },
            )
        observation = self.workspace.list_files().output
        history: list[dict[str, Any]] = []
        successful_runs: list[dict[str, Any]] = []
        failed_runs: list[dict[str, Any]] = []
        last_run_ok: bool | None = None
        last_result: WorkspaceActionResult | None = None
        previous_failure: tuple[str, str] | None = None
        for turn in range(1, self.max_steps + 1):
            history_text = json.dumps(history[-6:], ensure_ascii=False, indent=2)
            prompt = (
                f"EXPERIMENT TASK:\n{task}\n\n"
                f"RUNTIME CAPABILITIES:\n{self.workspace.capability_summary()}\n\n"
                f"RECENT ACTION HISTORY:\n{history_text[-self.observation_limit:]}\n\n"
                f"LATEST OBSERVATION:\n{observation[-self.observation_limit:]}\n\n"
                f"Successful Python runs: {len(successful_runs)}; failed runs: {len(failed_runs)}.\n"
                "Choose exactly one next action. Return only the JSON action object."
            )
            plan_step = f"{step_key}:plan:{turn}"
            raw = call_agent(agent, prompt, plan_step)
            try:
                action = parse_json_object(raw)
            except StructuredOutputError as first_error:
                repair_step = f"{plan_step}:action_repair"
                self.trace.log(
                    "code_experiment_action_repair",
                    step_key=plan_step,
                    repair_step_key=repair_step,
                    turn=turn,
                    outcome="retrying",
                    error=str(first_error),
                )
                repair_prompt = (
                    "Önceki yanıt geçerli bir CodeExperiment action JSON object değildi. "
                    "Matematiksel açıklama, markdown veya düz yazı verme. YALNIZ tek action JSON object döndür; "
                    "action write_file|patch_file|read_file|list_files|run_python|finish seçeneklerinden biri olsun.\n\n"
                    "ÖNCEKİ GEÇERSİZ YANIT:\n" + str(raw)[-self.observation_limit:]
                )
                repaired_raw = call_agent(agent, repair_prompt, repair_step)
                try:
                    action = parse_json_object(repaired_raw)
                except StructuredOutputError as second_error:
                    self.trace.log(
                        "code_experiment_action_repair",
                        step_key=plan_step,
                        repair_step_key=repair_step,
                        turn=turn,
                        outcome="failed",
                        error=str(second_error),
                    )
                    return ToolResult(
                        False,
                        "code_experiment",
                        error="agent eylem üretemedi: " + str(second_error),
                        metadata={
                            "status": "ACTION_FORMAT_ERROR",
                            "evidence_level": "COMPUTATION_ONLY",
                            "format_error": True,
                            "tool_unavailable": False,
                        },
                    )
                self.trace.log(
                    "code_experiment_action_repair",
                    step_key=plan_step,
                    repair_step_key=repair_step,
                    turn=turn,
                    outcome="recovered",
                )
            action_name = str(action.get("action") or "").lower()
            self.trace.log("code_experiment_action", step_key=step_key, turn=turn, agent=agent.name, model=agent.model, action=self._action_for_trace(action))
            if action_name == "finish":
                if not successful_runs or last_run_ok is not True:
                    reason = "finish reddedildi: en az bir başarılı run_python gerekli ve en son run_python başarılı olmalı."
                    self.trace.log("code_experiment_finish_rejected", step_key=step_key, turn=turn, successful_runs=len(successful_runs), failed_runs=len(failed_runs), last_run_ok=last_run_ok, reason=reason)
                    observation = reason
                    history.append({"turn": turn, "action": action, "result": {"ok": False, "error": reason}})
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
            self.trace.log("code_experiment_result", step_key=step_key, turn=turn, executed_action=action_name, result=result_payload)
            history.append({"turn": turn, "action": self._action_for_trace(action), "result": result_payload})
            if action_name == "run_python":
                evidence_record = {"ok": bool(last_result.ok), **dict(last_result.metadata or {})}
                last_run_ok = bool(last_result.ok)
                (successful_runs if last_result.ok else failed_runs).append(evidence_record)
            if infrastructure_failure(last_result):
                error = str(last_result.error or self.workspace.availability_reason or "container infrastructure unavailable")
                self.trace.log(
                    "tool_infrastructure_error",
                    step_key=f"{step_key}:plan:{turn}",
                    tool_step_key=step_key,
                    turn=turn,
                    agent=agent.name,
                    model=agent.model,
                    error=error.removeprefix("infrastructure: ").strip(),
                )
                return ToolResult(
                    False,
                    "code_experiment",
                    error=error if error.startswith("infrastructure:") else "infrastructure: " + error,
                    metadata={
                        "status": "INFRASTRUCTURE_ERROR",
                        "evidence_level": "COMPUTATION_ONLY",
                        "infrastructure_error": True,
                        "tool_unavailable": True,
                        "availability_reason": (last_result.metadata or {}).get("availability_reason") or error,
                        "failed_run_count": len(failed_runs),
                    },
                )
            action_signature = json.dumps(
                self._action_for_trace(action), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            current_failure = (action_signature, str(last_result.error or "")) if not last_result.ok else None
            if current_failure is not None and current_failure == previous_failure:
                reason = "aynı eylem aynı hatayı verdi; altyapı ya da kalıcı hata"
                self.trace.log(
                    "code_experiment_repeated_failure",
                    step_key=step_key,
                    turn=turn,
                    action=self._action_for_trace(action),
                    error=last_result.error,
                    reason=reason,
                )
                return ToolResult(
                    False,
                    "code_experiment",
                    error=reason + ": " + str(last_result.error or "bilinmeyen hata"),
                    metadata={
                        "status": "REPEATED_FAILURE",
                        "evidence_level": "COMPUTATION_ONLY",
                        "failed_run_count": len(failed_runs),
                    },
                )
            previous_failure = current_failure
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
