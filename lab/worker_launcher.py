from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import (
    ProjectBusyError,
    atomic_write_json,
    project_lock_is_live,
    project_lock_owner,
)
from lab.project_manager import ProjectManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _launch_guard(root: Path) -> Path:
    return root / "launch.guard"


def _claim_launch_guard(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    guard = _launch_guard(root)
    for _ in range(2):
        try:
            fd = os.open(str(guard), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                age = time.time() - guard.stat().st_mtime
            except OSError:
                age = 0
            if age > 30:
                try:
                    guard.unlink()
                except OSError:
                    pass
                continue
            raise ProjectBusyError("Bu proje için başka bir worker başlatma işlemi zaten sürüyor.")
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created_at": _now()}, handle)
            return guard
    raise ProjectBusyError("Worker launch guard alınamadı.")


def write_worker_request(project_root: str | Path, payload: dict[str, Any]) -> Path:
    root = Path(project_root)
    if project_lock_is_live(root):
        owner = project_lock_owner(root)
        raise ProjectBusyError(
            f"Aktif worker varken request/config değiştirilemez (pid={owner.get('pid', '?')})."
        )
    guard = _claim_launch_guard(root)
    path = root / "worker_request.json"
    try:
        atomic_write_json(path, payload)
    except Exception:
        guard.unlink(missing_ok=True)
        raise
    return path


def _windows_popen(command: list[str], kwargs: dict[str, Any]) -> subprocess.Popen:
    base_flags = 0
    base_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    base_flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        return subprocess.Popen(command, **kwargs, creationflags=base_flags | breakaway)
    except OSError:
        # Some parent jobs disallow BREAKAWAY_OK. Fall back to the strongest flags
        # available; stale-runtime recovery still makes a hard-killed child resumable.
        return subprocess.Popen(command, **kwargs, creationflags=base_flags)


def launch_theorem_worker(project_id: str, *, root: str | Path = "research_state") -> int:
    project_root = Path(root) / project_id
    request = project_root / "worker_request.json"
    if not request.exists():
        raise FileNotFoundError(f"worker_request.json bulunamadı: {request}")
    guard = _launch_guard(project_root)
    if not guard.exists():
        guard = _claim_launch_guard(project_root)
    if project_lock_is_live(project_root):
        guard.unlink(missing_ok=True)
        owner = project_lock_owner(project_root)
        raise ProjectBusyError(f"Bu proje zaten çalışıyor (pid={owner.get('pid', '?')}).")

    command = [sys.executable, "-m", "lab.worker", project_id]
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": dict(os.environ),
        "close_fds": os.name != "nt",
    }
    try:
        if os.name == "nt":
            proc = _windows_popen(command, kwargs)
        else:
            proc = subprocess.Popen(command, **kwargs, start_new_session=True)

        # Wait briefly for the *actual* worker to publish run.lock. This also
        # handles venv launcher stubs whose Popen.pid is not the final Python PID.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            owner = project_lock_owner(project_root)
            if owner.get("pid"):
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        return int(proc.pid)
    finally:
        guard.unlink(missing_ok=True)


def build_request_from_ui(
    *,
    project_id: str,
    problem: str,
    iterations: int,
    literature_query: str | None,
    checkpoint_every: int,
    agents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pm = ProjectManager()
    info = pm.get(project_id)
    return {
        "request_version": 1,
        "project_id": project_id,
        "project_uuid": info.project_uuid,
        "problem": problem,
        "iterations": int(iterations),
        "literature_query": literature_query,
        "checkpoint_every": int(checkpoint_every),
        "agents": agents,
        "created_at": _now(),
    }
