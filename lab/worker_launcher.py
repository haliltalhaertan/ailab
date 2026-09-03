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


EXPERIMENT_METHODS = {"theorem_lab", "research_loop", "debate", "pipeline", "panel"}


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


def _windows_popen(
    command: list[str], kwargs: dict[str, Any]
) -> tuple[subprocess.Popen, bool]:
    base_flags = 0
    base_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    base_flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        return (
            subprocess.Popen(command, **kwargs, creationflags=base_flags | breakaway),
            True,
        )
    except OSError:
        # Some Windows parent jobs disallow BREAKAWAY_OK. Retry without it and
        # make the weaker launch mode observable instead of silently claiming it.
        return subprocess.Popen(command, **kwargs, creationflags=base_flags), False


def launch_worker(project_id: str, *, root: str | Path = "research_state") -> int:
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
        breakaway: bool | None
        if os.name == "nt":
            proc, breakaway = _windows_popen(command, kwargs)
        else:
            proc = subprocess.Popen(command, **kwargs, start_new_session=True)
            breakaway = None

        # Launcher identity is deliberately separate from worker.json. The
        # worker itself publishes its real PID only after it owns run.lock.
        atomic_write_json(
            project_root / "worker_launch.json",
            {
                "launcher_pid": int(proc.pid),
                "launched_at": _now(),
                "breakaway": breakaway,
                "platform": os.name,
            },
        )

        # Wait briefly for the actual worker to publish run.lock. This handles
        # venv launcher stubs whose Popen.pid is not the final Python PID.
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


def launch_theorem_worker(project_id: str, *, root: str | Path = "research_state") -> int:
    """Backward-compatible alias for callers that still use the old name."""

    return launch_worker(project_id, root=root)


def build_request_from_ui(
    *,
    project_id: str,
    agents: dict[str, dict[str, Any]] | list[dict[str, Any]],
    problem: str | None = None,
    iterations: int | None = None,
    literature_query: str | None = None,
    checkpoint_every: int = 2,
    experiment_method: str = "theorem_lab",
    experiment_name: str | None = None,
    optional_agents: dict[str, dict[str, Any]] | None = None,
    param: int | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    if experiment_method not in EXPERIMENT_METHODS:
        raise ValueError(f"Unsupported experiment_method: {experiment_method}")
    pm = ProjectManager()
    info = pm.get(project_id)
    resolved_prompt = str(prompt if prompt is not None else problem or "")
    resolved_param = int(param if param is not None else iterations if iterations is not None else 0)
    payload: dict[str, Any] = {
        "request_version": 2,
        "project_id": project_id,
        "project_uuid": info.project_uuid,
        "experiment_method": experiment_method,
        "experiment_name": experiment_name or (
            "Teorem Araştırması" if experiment_method == "theorem_lab" else experiment_method
        ),
        "agents": agents,
        "optional_agents": optional_agents or {},
        "param": resolved_param,
        "prompt": resolved_prompt,
        "created_at": _now(),
    }
    if experiment_method == "theorem_lab":
        payload.update(
            {
                "problem": resolved_prompt,
                "iterations": resolved_param or 5,
                "literature_query": literature_query,
                "checkpoint_every": int(checkpoint_every),
            }
        )
    return payload
