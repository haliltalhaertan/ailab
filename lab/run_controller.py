from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import ProjectRunLock, atomic_write_json, read_json_tolerant
from lab.trace import Trace


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


def retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 425, 429}:
        return True
    if isinstance(status, int) and 500 <= status <= 599:
        return True
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

    def runtime(self) -> dict[str, Any]:
        return read_json(
            self.runtime_path,
            {
                "status": "NEW",
                "completed_iterations": 0,
                "next_task": "",
                "current_iteration": 0,
                "current_step": "",
                "last_error": "",
            },
        )

    def set_runtime(self, **updates: Any) -> dict[str, Any]:
        value = self.runtime()
        value.update(updates)
        now = now_iso()
        value["pid"] = os.getpid()
        value["updated_at"] = now
        # Every runtime mutation is also proof-of-life. This avoids a long-lived
        # RUNNING state whose latest cursor update is newer than its heartbeat.
        value["heartbeat_at"] = now
        atomic_json(self.runtime_path, value)
        self.trace.log("runtime_state", **value)
        self._last_heartbeat_monotonic = time.monotonic()
        return value

    def heartbeat(self, *, min_interval_s: float = 15.0) -> None:
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

    def backoff(self, attempt: int) -> int:
        wait_s = min(2 ** max(0, attempt - 1), 8)
        time.sleep(wait_s)
        return wait_s
