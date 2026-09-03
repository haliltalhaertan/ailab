from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from lab.integrity import atomic_write_json, process_alive, read_json_tolerant
from lab.runtime_health import cleanup_stale_run, normalize_runtime, worker_liveness


UI_SETTINGS_FILE = "ui_settings.json"


def project_ui_settings_path(project_root: str | Path) -> Path:
    return Path(project_root) / UI_SETTINGS_FILE


def load_project_ui_settings(project_root: str | Path) -> dict[str, Any]:
    raw = read_json_tolerant(project_ui_settings_path(project_root), {})
    return dict(raw) if isinstance(raw, dict) else {}


def save_project_ui_settings(
    project_root: str | Path,
    *,
    agents: dict[str, dict[str, Any]] | None = None,
    orchestrator_default: dict[str, Any] | None = None,
) -> Path:
    root = Path(project_root)
    payload = load_project_ui_settings(root)
    if agents is not None:
        payload["agents"] = {str(role): dict(value) for role, value in agents.items()}
    if orchestrator_default is not None:
        payload["orchestrator_default"] = dict(orchestrator_default)
    payload["version"] = 1
    path = project_ui_settings_path(root)
    atomic_write_json(path, payload)
    return path


def configured_model(project_root: str | Path, role: str) -> str | None:
    settings = load_project_ui_settings(project_root)
    agents = settings.get("agents")
    if isinstance(agents, dict):
        raw = agents.get(role)
        if isinstance(raw, dict) and str(raw.get("model") or "").strip():
            return str(raw["model"]).strip()
    generic = settings.get("orchestrator_default")
    if isinstance(generic, dict) and str(generic.get("model") or "").strip():
        return str(generic["model"]).strip()
    return None


def configured_effort(project_root: str | Path, role: str) -> str | None:
    settings = load_project_ui_settings(project_root)
    agents = settings.get("agents")
    if isinstance(agents, dict):
        raw = agents.get(role)
        if isinstance(raw, dict) and raw.get("reasoning_effort") is not None:
            return str(raw.get("reasoning_effort") or "").strip() or None
    generic = settings.get("orchestrator_default")
    if isinstance(generic, dict) and generic.get("reasoning_effort") is not None:
        return str(generic.get("reasoning_effort") or "").strip() or None
    return None


def local_storage_summary(project_root: str | Path, runs_root: str | Path) -> dict[str, str]:
    root = Path(project_root).resolve()
    runs = Path(runs_root).resolve()
    return {
        "project_root": str(root),
        "runs_root": str(runs),
        "latest_result": str((root / "worker_result.md").resolve()),
        "checkpoints": str((root / "checkpoints").resolve()),
    }


def delete_run_history(run_summaries: list[dict[str, Any]], runs_root: str | Path) -> int:
    """Delete exactly the run directories represented by ProjectManager summaries.

    The index is rewritten without those run IDs so a full local delete does not
    leave dead navigation rows behind.
    """

    run_ids: set[str] = set()
    deleted = 0
    for row in run_summaries:
        run_id = str(row.get("run") or "").strip()
        run_dir = Path(str(row.get("run_dir") or "")) if row.get("run_dir") else None
        if run_id:
            run_ids.add(run_id)
        if run_dir is not None and run_dir.exists():
            shutil.rmtree(run_dir)
            deleted += 1

    index_path = Path(runs_root) / "index.jsonl"
    if run_ids and index_path.exists():
        kept: list[str] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if isinstance(row, dict) and str(row.get("run_id") or "") in run_ids:
                continue
            kept.append(line)
        index_path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    return deleted


def _worker_pid(project_root: Path) -> int:
    health = worker_liveness(project_root)
    for value in (health.get("worker_pid"), health.get("lock_pid")):
        try:
            pid = int(value or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            return pid
    return 0


def _kill_process_group(pid: int, sig: int) -> None:
    """Signal a POSIX process group without exposing Windows-only type errors."""

    killpg = getattr(os, "killpg", None)
    if callable(killpg):
        killpg(pid, sig)
    else:
        os.kill(pid, sig)


def force_stop_worker(project_root: str | Path, *, wait_s: float = 2.0) -> bool:
    """Immediately terminate the detached worker and mark the run interrupted.

    Normal DURDUR remains cooperative through stop.flag. This function is the
    explicit operator escape hatch for a user who wants to stop immediately.
    It fails closed: if the PID is still alive after the force attempt, the
    project lock/runtime are left intact and the caller must not delete state.
    """

    root = Path(project_root)
    pid = _worker_pid(root)
    if pid <= 0:
        return False

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            _kill_process_group(pid, int(signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    deadline = time.monotonic() + max(0.0, float(wait_s))
    while time.monotonic() < deadline and process_alive(pid):
        time.sleep(0.05)

    if process_alive(pid) and os.name != "nt":
        hard_signal = int(getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            _kill_process_group(pid, hard_signal)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, hard_signal)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        hard_deadline = time.monotonic() + 0.5
        while time.monotonic() < hard_deadline and process_alive(pid):
            time.sleep(0.05)

    if process_alive(pid):
        return False

    raw = read_json_tolerant(root / "runtime.json", {})
    runtime = dict(raw) if isinstance(raw, dict) else {}
    derived = normalize_runtime(root, runtime, heartbeat_timeout_s=0.0)
    if str(derived.get("status") or "").upper() == "STALE_RUNNING":
        try:
            cleanup_stale_run(root)
            return True
        except RuntimeError:
            pass

    (root / "run.lock").unlink(missing_ok=True)
    if runtime:
        runtime["status"] = "INTERRUPTED"
        runtime["last_error"] = "Worker kullanıcı tarafından zorla durduruldu; partial/cache dosyaları korunmuş olabilir."
        atomic_write_json(root / "runtime.json", runtime)
    return True
