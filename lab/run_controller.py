from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import ProjectRunLock
from lab.trace import Trace


class ResearchStopped(RuntimeError):
    pass


class ResearchPaused(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


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
            "timeout", "timed out", "connection", "temporarily unavailable", "rate limit", "429", "502", "503", "504"
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
        value["updated_at"] = now_iso()
        atomic_json(self.runtime_path, value)
        self.trace.log("runtime_state", **value)
        return value

    def check_stop(self) -> None:
        if self.stop_path.exists():
            raise ResearchStopped("Kullanıcı durdurma isteği gönderdi.")

    def clear_stale_stop(self) -> None:
        self.stop_path.unlink(missing_ok=True)

    def backoff(self, attempt: int) -> int:
        wait_s = min(2 ** max(0, attempt - 1), 8)
        time.sleep(wait_s)
        return wait_s
