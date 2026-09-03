from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import uuid
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_fingerprint(kind: str, value: Any) -> str:
    payload = f"{kind}\n{canonical_json(value)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    attempts: int = 5,
    initial_backoff_s: float = 0.05,
) -> None:
    """Durably replace a text file, retrying Windows sharing violations.

    Temporary names include the process id so concurrent workers never share a
    fixed ``*.tmp`` path. Permission/sharing violations are retried a bounded
    five times by default with a fixed 50 ms backoff, then surfaced to callers.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        retries = max(1, int(attempts))
        delay = max(0.0, float(initial_backoff_s))
        last_error: OSError | None = None
        for attempt in range(retries):
            try:
                os.replace(tmp, target)
                return
            except PermissionError as exc:
                last_error = exc
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {5, 32}:
                    raise
                last_error = exc
            if attempt + 1 < retries:
                time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise OSError(f"atomic replace failed for {target}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def read_json_tolerant(path: str | Path, default: Any) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, PermissionError):
        return default


def _windows_process_alive(pid: int) -> bool:
    """Return liveness using the Windows process table, not ``os.kill(pid, 0)``.

    CPython's Windows ``os.kill`` semantics are not a POSIX-style existence
    probe for detached processes and can misclassify both live and exited
    workers. Query a fresh process handle and its exit code instead. Access
    denied is treated as live so lock ownership fails closed.
    """

    import ctypes

    win_dll = getattr(ctypes, "WinDLL", None)
    get_last_error = getattr(ctypes, "get_last_error", None)
    if win_dll is None or get_last_error is None:
        return False

    kernel32 = win_dll("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5

    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(process_query_limited_information, 0, int(pid))
    if not handle:
        return int(get_last_error()) == error_access_denied

    try:
        exit_code = ctypes.c_ulong(0)
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            # We opened the process but cannot query its state. Conservatively
            # treat it as live so a project lock is never stolen on uncertainty.
            return True
        return int(exit_code.value) == still_active
    finally:
        close_handle(handle)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> dict[str, Any]:
    raw = read_json_tolerant(path, {})
    return raw if isinstance(raw, dict) else {}


def project_lock_owner(project_root: str | Path) -> dict[str, Any]:
    return _read_lock(Path(project_root) / "run.lock")


def project_lock_is_live(project_root: str | Path) -> bool:
    raw = project_lock_owner(project_root)
    if not raw:
        return False
    host = str(raw.get("host") or "")
    try:
        pid = int(raw.get("pid") or 0)
    except Exception:
        pid = 0
    if host and host != socket.gethostname():
        return True
    return process_alive(pid)


class ProjectBusyError(RuntimeError):
    pass


class ProjectRunLock:
    """Cross-process project lock backed by atomic O_EXCL file creation."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.path = self.project_root / "run.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False
        self._depth = 0

    def _payload(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at_epoch": time.time(),
        }

    def _read(self) -> dict[str, Any]:
        return _read_lock(self.path)

    def _stale(self, raw: dict[str, Any]) -> bool:
        host = str(raw.get("host") or "")
        try:
            pid = int(raw.get("pid") or 0)
        except Exception:
            pid = 0
        return bool(host and host == socket.gethostname() and pid and not process_alive(pid))

    def acquire(self) -> "ProjectRunLock":
        if self.acquired:
            self._depth += 1
            return self
        self.project_root.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raw = self._read()
                if self._stale(raw):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                owner = f"pid={raw.get('pid', '?')} host={raw.get('host', '?')}"
                raise ProjectBusyError(f"Bu proje başka bir process tarafından çalıştırılıyor ({owner}).")
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._payload(), handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                self.acquired = True
                self._depth = 1
                return self
        raise ProjectBusyError("Proje run kilidi alınamadı.")

    def release(self) -> None:
        if not self.acquired:
            return
        if self._depth > 1:
            self._depth -= 1
            return
        raw = self._read()
        if str(raw.get("token") or "") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False
        self._depth = 0

    def __enter__(self) -> "ProjectRunLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class EvidenceSigner:
    """HMAC seal for local evidence/cache tamper detection.

    ``LAB_EVIDENCE_HMAC_KEY`` is the stronger mode because the key need not live
    beside project data. Without it, a random per-project key is created with
    O_EXCL and stored in ``.evidence_hmac.key``. Local-key mode detects accidental
    or naive manual edits; it is not protection from an administrator who can
    read/replace both the project and its key.
    """

    ENV_NAME = "LAB_EVIDENCE_HMAC_KEY"

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.root.mkdir(parents=True, exist_ok=True)
        external = os.environ.get(self.ENV_NAME)
        if external:
            self.key = external.encode("utf-8")
            self.mode = "EXTERNAL_ENV"
            self.key_path: Path | None = None
        else:
            self.key_path = self.root / ".evidence_hmac.key"
            self._ensure_local_key()
            self.key = self.key_path.read_text(encoding="utf-8").strip().encode("utf-8")
            self.mode = "LOCAL_PROJECT_KEY"

    def _ensure_local_key(self) -> None:
        assert self.key_path is not None
        if self.key_path.exists():
            return
        secret = secrets.token_hex(32)
        try:
            fd = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secret)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass

    def sign(self, kind: str, value: Any) -> str:
        payload = f"{kind}\n{canonical_json(value)}".encode("utf-8")
        return hmac.new(self.key, payload, hashlib.sha256).hexdigest()

    def verify(self, kind: str, value: Any, signature: str | None) -> bool:
        if not signature:
            return False
        expected = self.sign(kind, value)
        return hmac.compare_digest(expected, str(signature))
