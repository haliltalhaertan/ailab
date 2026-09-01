from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import atomic_write_json, process_alive, project_lock_owner, read_json_tolerant


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _recent(value: str, grace_s: float) -> bool:
    parsed = _parse_time(value)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() <= float(grace_s)


def worker_liveness(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    lock = project_lock_owner(root)
    worker = read_json_tolerant(root / "worker.json", {})
    worker = worker if isinstance(worker, dict) else {}

    lock_pid = 0
    try:
        lock_pid = int(lock.get("pid") or 0)
    except Exception:
        pass
    actual_pid = 0
    for field in ("actual_pid", "pid"):
        try:
            actual_pid = int(worker.get(field) or 0)
        except Exception:
            actual_pid = 0
        if actual_pid:
            break

    lock_live = process_alive(lock_pid) if lock_pid else False
    worker_live = process_alive(actual_pid) if actual_pid else False
    return {
        "lock_owner": lock,
        "worker": worker,
        "lock_pid": lock_pid,
        "worker_pid": actual_pid,
        "lock_live": lock_live,
        "worker_live": worker_live,
        "live": bool(lock_live or worker_live),
    }


def normalize_runtime(
    project_root: str | Path,
    runtime: dict[str, Any] | None = None,
    *,
    persist: bool = True,
    startup_grace_s: float = 15.0,
) -> dict[str, Any]:
    """Convert impossible stale RUNNING state into recoverable INTERRUPTED.

    The project lock owner is authoritative because on Windows a venv launcher
    stub PID can differ from the actual Python worker PID. During the short
    STARTING window we tolerate a missing lock so the UI does not race the child.
    """

    root = Path(project_root)
    current = dict(runtime or {})
    if not current:
        raw = read_json_tolerant(root / "runtime.json", {})
        current = dict(raw) if isinstance(raw, dict) else {}
    if str(current.get("status") or "").upper() != "RUNNING":
        return current

    health = worker_liveness(root)
    if health["live"]:
        return current

    worker = health["worker"]
    if str(worker.get("status") or "").upper() in {"STARTING", "RUNNING"} and _recent(
        str(worker.get("launched_at") or worker.get("started_at") or ""), startup_grace_s
    ):
        return current

    interrupted = dict(current)
    interrupted.update(
        {
            "status": "INTERRUPTED",
            "last_error": "Worker process is no longer alive; cached/partial state was preserved and the run can be resumed.",
            "interrupted_at": _now(),
            "updated_at": _now(),
            "stale_worker_pid": health.get("worker_pid") or health.get("lock_pid") or 0,
        }
    )
    if persist:
        atomic_write_json(root / "runtime.json", interrupted)
    return interrupted
