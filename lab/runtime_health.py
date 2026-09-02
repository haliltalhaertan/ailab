from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import atomic_write_json, process_alive, project_lock_owner, read_json_tolerant


STALE_HEARTBEAT_S = 120.0


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


def _age_seconds(value: str) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def worker_liveness(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    lock = project_lock_owner(root)
    worker = read_json_tolerant(root / "worker.json", {})
    worker = worker if isinstance(worker, dict) else {}

    try:
        lock_pid = int(lock.get("pid") or 0)
    except Exception:
        lock_pid = 0
    try:
        worker_pid = int(worker.get("pid") or worker.get("actual_pid") or 0)
    except Exception:
        worker_pid = 0

    lock_host = str(lock.get("host") or "")
    if lock and lock_host and lock_host != socket.gethostname():
        # We cannot prove a PID on another host is dead from this process.
        lock_live = True
    else:
        lock_live = process_alive(lock_pid) if lock_pid else False
    worker_live = process_alive(worker_pid) if worker_pid else False
    return {
        "lock_owner": lock,
        "worker": worker,
        "lock_pid": lock_pid,
        "worker_pid": worker_pid,
        "lock_live": lock_live,
        "worker_live": worker_live,
    }


def stale_running_reason(
    project_root: str | Path,
    runtime: dict[str, Any] | None = None,
    *,
    heartbeat_timeout_s: float = STALE_HEARTBEAT_S,
) -> str:
    """Return why a persisted RUNNING state is stale, or an empty string."""

    root = Path(project_root)
    current = dict(runtime or {})
    if not current:
        raw = read_json_tolerant(root / "runtime.json", {})
        current = dict(raw) if isinstance(raw, dict) else {}
    if str(current.get("status") or "").upper() != "RUNNING":
        return ""

    health = worker_liveness(root)
    if not health["lock_owner"]:
        return "run.lock is missing"
    if not health["lock_live"]:
        return f"run.lock owner pid {health['lock_pid'] or '?'} is not alive"

    heartbeat = str(current.get("heartbeat_at") or "")
    age = _age_seconds(heartbeat)
    if age is None:
        return "heartbeat_at is missing or invalid"
    if age > float(heartbeat_timeout_s):
        return f"heartbeat_at is stale ({age:.1f}s > {float(heartbeat_timeout_s):.1f}s)"
    return ""


def normalize_runtime(
    project_root: str | Path,
    runtime: dict[str, Any] | None = None,
    *,
    persist: bool = False,
    heartbeat_timeout_s: float = STALE_HEARTBEAT_S,
) -> dict[str, Any]:
    """Expose stale RUNNING as STALE_RUNNING without silently mutating state.

    ``runtime.json`` remains RUNNING until the user explicitly cleans the stale
    run or a new worker safely takes ownership. This makes stale detection a
    diagnostic/UI state rather than an implicit destructive recovery step.
    ``persist`` is retained for API compatibility and intentionally ignored.
    """

    del persist
    root = Path(project_root)
    current = dict(runtime or {})
    if not current:
        raw = read_json_tolerant(root / "runtime.json", {})
        current = dict(raw) if isinstance(raw, dict) else {}
    reason = stale_running_reason(root, current, heartbeat_timeout_s=heartbeat_timeout_s)
    if not reason:
        return current

    health = worker_liveness(root)
    derived = dict(current)
    derived.update(
        {
            "status": "STALE_RUNNING",
            "stale_reason": reason,
            "stale_worker_pid": health.get("worker_pid") or health.get("lock_pid") or 0,
        }
    )
    return derived


def cleanup_stale_run(project_root: str | Path) -> dict[str, Any]:
    """Explicitly convert a detected stale run to resumable INTERRUPTED."""

    root = Path(project_root)
    raw = read_json_tolerant(root / "runtime.json", {})
    current = dict(raw) if isinstance(raw, dict) else {}
    derived = normalize_runtime(root, current)
    if str(derived.get("status") or "").upper() != "STALE_RUNNING":
        raise RuntimeError("Run is not stale; refusing to remove a live project lock.")

    (root / "run.lock").unlink(missing_ok=True)
    now = _now()
    cleaned = dict(current)
    cleaned.update(
        {
            "status": "INTERRUPTED",
            "last_error": "Stale worker state was explicitly cleaned; cached/partial work is preserved and can be resumed.",
            "interrupted_at": now,
            "updated_at": now,
            "heartbeat_at": now,
            "stale_reason": derived.get("stale_reason", ""),
            "stale_worker_pid": derived.get("stale_worker_pid", 0),
        }
    )
    atomic_write_json(root / "runtime.json", cleaned)
    return cleaned
