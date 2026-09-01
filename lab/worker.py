from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from lab import TheoremResearchLab
from lab.agent import Agent
from lab.integrity import (
    ProjectBusyError,
    ProjectRunLock,
    atomic_write_json,
    atomic_write_text,
    read_json_tolerant,
)
from lab.project_manager import ProjectManager
from lab.research_state import ResearchState
from lab.trace import Trace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    value = read_json_tolerant(path, None)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {path}")
    return value


def _agent(role: str, raw: dict) -> Agent:
    return Agent(
        name=str(raw.get("name") or role),
        system_prompt=str(raw.get("system_prompt") or raw.get("prompt") or ""),
        model=str(raw.get("model") or ""),
        temperature=float(raw.get("temperature", raw.get("temp", 0.2))),
        max_tokens=raw.get("max_tokens"),
        reasoning_effort=raw.get("reasoning_effort"),
    )


def _write_worker(root: Path, **updates) -> None:
    path = root / "worker.json"
    current = read_json_tolerant(path, {})
    current = dict(current) if isinstance(current, dict) else {}
    current.update(updates)
    current["updated_at"] = _now()
    atomic_write_json(path, current)


def _mark_runtime_error(root: Path, exc: Exception) -> None:
    path = root / "runtime.json"
    current = read_json_tolerant(path, {})
    current = dict(current) if isinstance(current, dict) else {}
    now = _now()
    current.update(
        {
            "status": "PAUSED_ERROR",
            "last_error": repr(exc),
            "pid": os.getpid(),
            "updated_at": now,
            "heartbeat_at": now,
        }
    )
    atomic_write_json(path, current)


def run_project(project_id: str) -> int:
    load_dotenv()
    pm = ProjectManager()
    info = pm.get(project_id)
    root = pm.project_root(project_id)

    # The worker owns the project lock before it reads the mutable request or
    # touches worker/project/runtime/config/trace state. A losing worker exits
    # without overwriting the active worker's files or clearing its stop flag.
    lock = ProjectRunLock(root)
    try:
        lock.acquire()
    except ProjectBusyError:
        return 3

    trace: Trace | None = None
    result = ""
    exit_code = 0
    try:
        request_path = root / "worker_request.json"
        request = _read(request_path)
        if str(request.get("project_uuid") or "") != info.project_uuid:
            raise RuntimeError("worker request project_uuid does not match current project identity")
        raw_agents = request.get("agents") or {}
        if not isinstance(raw_agents, dict):
            raise ValueError("worker request agents must be an object")
        agents = {
            role: _agent(role, raw)
            for role, raw in raw_agents.items()
            if isinstance(raw, dict)
        }
        required = {
            "ResearchManager",
            "Theorist",
            "AdversarialCritic",
            "VerificationEngineer",
            "IndependentAuditor",
        }
        missing = required - set(agents)
        if missing:
            raise ValueError(f"Missing worker agents: {sorted(missing)}")

        state = ResearchState(root)
        trace = Trace("theorem-worker")
        trace.log(
            "project_context",
            project_id=project_id,
            project_uuid=info.project_uuid,
            title=info.title,
            experiment="Teorem Araştırması",
        )
        _write_worker(
            root,
            status="RUNNING",
            run_id=trace.run_id,
            actual_pid=os.getpid(),
            started_at=_now(),
        )
        pm.touch(project_id, status="RUNNING")

        lab = TheoremResearchLab(
            trace,
            state,
            code_experiment_settings_override=(
                request.get("code_experiment")
                if isinstance(request.get("code_experiment"), dict)
                else None
            ),
        )
        # Share the already-held lock with the engine. ProjectRunLock is
        # re-entrant for this object, so nested engine contexts do not race.
        lab.controller.lock = lock
        result = lab.run(
            str(request.get("problem") or ""),
            manager=agents["ResearchManager"],
            proposer=agents["Theorist"],
            code_agent=agents.get("CodeExperimentAgent"),
            critic=agents["AdversarialCritic"],
            verifier=agents["VerificationEngineer"],
            literature_agent=agents.get("LiteratureScout"),
            auditor=agents["IndependentAuditor"],
            iterations=int(request.get("iterations", 5)),
            literature_query=request.get("literature_query"),
            checkpoint_every=int(request.get("checkpoint_every", 2)),
        )
        runtime_path = root / "runtime.json"
        runtime = _read(runtime_path) if runtime_path.exists() else {}
        status = str(runtime.get("status") or "COMPLETED")
        pm.touch(project_id, status=status)
        _write_worker(root, status=status, finished_at=_now(), actual_pid=os.getpid())
    except Exception as exc:
        exit_code = 2
        result = f"Worker failed: {exc}"
        try:
            _mark_runtime_error(root, exc)
        except Exception:
            pass
        try:
            pm.touch(project_id, status="PAUSED_ERROR")
        except Exception:
            pass
        try:
            _write_worker(
                root,
                status="PAUSED_ERROR",
                error=repr(exc),
                finished_at=_now(),
                actual_pid=os.getpid(),
            )
        except Exception:
            pass
        if trace is not None:
            trace.log("worker_error", error=repr(exc))
    finally:
        try:
            atomic_write_text(root / "worker_result.md", result)
        except Exception:
            pass
        if trace is not None and not trace.closed:
            try:
                summary = trace.close()
                _write_worker(root, summary=str(summary), run_id=trace.run_id, actual_pid=os.getpid())
            except Exception:
                pass
        lock.release()
    return exit_code


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m lab.worker <project_id>")
    raise SystemExit(run_project(sys.argv[1]))


if __name__ == "__main__":
    main()
