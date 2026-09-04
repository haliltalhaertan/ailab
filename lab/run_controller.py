from __future__ import annotations

import errno
import os
import threading
import time

import httpx
from openai import APIConnectionError, APITimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import ProjectRunLock, atomic_write_json, read_json_tolerant
from lab.trace import Trace


RESEARCH_PHASES = {
    "LITERATURE",
    "FORMALIZATION",
    "PILOT",
    "DISCOVERY",
    "FALSIFICATION",
    "PROOF",
    "PUBLICATION",
}
DEFAULT_RESEARCH_PHASE = "LITERATURE"


class ResearchStopped(RuntimeError):
    pass


class ResearchPaused(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def read_json(path: Path, default: Any) -> Any:
    return read_json_tolerant(path, default)


def default_runtime() -> dict[str, Any]:
    return {
        "status": "NEW",
        "research_phase": DEFAULT_RESEARCH_PHASE,
        "completed_iterations": 0,
        "next_task": "",
        "current_iteration": 0,
        "current_step": "",
        "last_error": "",
    }


def normalize_research_phase(value: Any) -> str:
    phase = str(value or DEFAULT_RESEARCH_PHASE).upper()
    if phase not in RESEARCH_PHASES:
        raise ValueError(f"invalid research_phase: {phase}")
    return phase


def set_research_phase(project_root: str | Path, phase: str) -> dict[str, Any]:
    """Persist a scientific workflow phase without changing execution status."""

    root = Path(project_root)
    path = root / "runtime.json"
    raw = read_json_tolerant(path, None)
    current = dict(raw) if isinstance(raw, dict) else default_runtime()
    current.setdefault("status", "NEW")
    current["research_phase"] = normalize_research_phase(phase)
    current["updated_at"] = now_iso()
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, current)
    return current


def _exception_chain(exc: BaseException):
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def retryable(exc: Exception) -> bool:
    transient_httpx = (
        httpx.ReadError,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.TimeoutException,
        httpx.PoolTimeout,
    )
    transient_errno = {errno.ECONNRESET, errno.ETIMEDOUT}
    transient_winerror = {10053, 10054, 10060}
    chain = list(_exception_chain(exc))
    for current in chain:
        status = _http_status(current)
        if status in {408, 409, 425, 429} or (isinstance(status, int) and 500 <= status <= 599):
            return True
        if isinstance(current, (APIConnectionError, APITimeoutError, ConnectionResetError, *transient_httpx)):
            return True
        if isinstance(current, OSError):
            if getattr(current, "winerror", None) in transient_winerror:
                return True
            if getattr(current, "errno", None) in transient_errno:
                return True

    # Deterministic/user-data errors must not become retryable merely because
    # their localized text happens to contain a network-looking word.
    if isinstance(exc, (ValueError, TypeError, KeyError, AssertionError)):
        return False

    # Compatibility fallback for third-party wrappers that erase exception types.
    text = " ".join(str(current).lower() for current in chain)
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection",
            "temporarily unavailable",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
        )
    )


class RunController:
    def __init__(self, project_root: str | Path, trace: Trace, *, max_retries: int = 3):
        self.root = Path(project_root)
        self.trace = trace
        self.runtime_path = self.root / "runtime.json"
        self.config_path = self.root / "run_config.json"
        self.stop_path = self.root / "stop.flag"
        self.max_retries = max(1, int(max_retries))
        self.lock = ProjectRunLock(self.root)
        self._last_heartbeat_monotonic = 0.0
        # Public only inside the worker/runtime layer: WorkerRuntimeBridge and
        # foreground controller writes share this exact RLock so heartbeat
        # read-modify-write cycles cannot overwrite newer theorem cursor state.
        self.write_lock = threading.RLock()

    def runtime(self) -> dict[str, Any]:
        with self.write_lock:
            value = read_json(self.runtime_path, default_runtime())
            current = dict(value) if isinstance(value, dict) else default_runtime()
            current.setdefault("research_phase", DEFAULT_RESEARCH_PHASE)
            return current

    def set_runtime(self, **updates: Any) -> dict[str, Any]:
        with self.write_lock:
            value = self.runtime()
            if "research_phase" in updates:
                updates["research_phase"] = normalize_research_phase(updates["research_phase"])
            value.update(updates)
            now = now_iso()
            value["pid"] = os.getpid()
            value["updated_at"] = now
            value["heartbeat_at"] = now
            atomic_json(self.runtime_path, value)
            self.trace.log("runtime_state", **value)
            self._last_heartbeat_monotonic = time.monotonic()
            return value

    def set_research_phase(self, phase: str) -> dict[str, Any]:
        return self.set_runtime(research_phase=phase)

    def heartbeat(self, *, min_interval_s: float = 15.0) -> None:
        with self.write_lock:
            now_mono = time.monotonic()
            if now_mono - self._last_heartbeat_monotonic < float(min_interval_s):
                return
            value = self.runtime()
            if str(value.get("status") or "").upper() != "RUNNING":
                return
            now = now_iso()
            value["pid"] = os.getpid()
            value["heartbeat_at"] = now
            value["updated_at"] = now
            atomic_json(self.runtime_path, value)
            self._last_heartbeat_monotonic = now_mono

    def check_stop(self) -> None:
        self.heartbeat()
        if self.stop_path.exists():
            raise ResearchStopped("Kullanıcı durdurma isteği gönderdi.")

    def clear_stale_stop(self) -> None:
        self.stop_path.unlink(missing_ok=True)
