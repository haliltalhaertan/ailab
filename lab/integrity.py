from __future__ import annotations

import hashlib
import json
import os
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


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ProjectBusyError(RuntimeError):
    pass


class ProjectRunLock:
    """Cross-process project lock backed by atomic O_EXCL file creation.

    The lock is intentionally project-scoped, not run-scoped: only one theorem
    workflow may mutate runtime/cache/state for a project at a time. A stale lock
    owned by a dead process on the same host is reclaimed safely.
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.path = self.project_root / "run.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _payload(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at_epoch": time.time(),
        }

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _stale(self, raw: dict[str, Any]) -> bool:
        host = str(raw.get("host") or "")
        try:
            pid = int(raw.get("pid") or 0)
        except Exception:
            pid = 0
        return bool(host and host == socket.gethostname() and pid and not _process_alive(pid))

    def acquire(self) -> "ProjectRunLock":
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
                raise ProjectBusyError(
                    f"Bu proje başka bir process tarafından çalıştırılıyor ({owner})."
                )
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._payload(), handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                self.acquired = True
                return self
        raise ProjectBusyError("Proje run kilidi alınamadı.")

    def release(self) -> None:
        if not self.acquired:
            return
        raw = self._read()
        if str(raw.get("token") or "") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> "ProjectRunLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
