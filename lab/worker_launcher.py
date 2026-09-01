from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.project_manager import ProjectManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_worker_request(project_root: str | Path, payload: dict[str, Any]) -> Path:
    root = Path(project_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "worker_request.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def launch_theorem_worker(project_id: str, *, root: str | Path = "research_state") -> int:
    project_root = Path(root) / project_id
    request = project_root / "worker_request.json"
    if not request.exists():
        raise FileNotFoundError(f"worker_request.json bulunamadı: {request}")
    command = [sys.executable, "-m", "lab.worker", project_id]
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": dict(os.environ),
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    info = {
        "pid": proc.pid,
        "project_id": project_id,
        "launched_at": _now(),
        "command": command,
        "status": "STARTING",
    }
    (project_root / "worker.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(proc.pid)


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
