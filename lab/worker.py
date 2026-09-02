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


def _write_worker(root: Path, *, pid: int, run_id: str, launched_at: str) -> None:
    """Publish worker identity only; execution status lives in runtime.json."""

    atomic_write_json(
        root / "worker.json",
        {
            "pid": int(pid),
            "run_id": str(run_id),
            "launched_at": str(launched_at),
        },
    )


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
    root = pm.project_root(project_id)

    # Lock first. A losing worker must not read/overwrite the active request,
    # config, stop flag, worker identity, project metadata or runtime state.
    lock = ProjectRunLock(root)
    try:
        lock.acquire()
    except ProjectBusyError as exc:
        try:
            atomic_write_json(
                root / "worker_busy.json",
                {
                    "pid": os.getpid(),
                    "rejected_at": _now(),
                    "error": str(exc),
                },
            )
        except Exception:
            pass
        return 3

    trace: Trace | None = None
    result = ""
    exit_code = 0
    try:
        info = pm.get(project_id)
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
        launched_at = _now()
        _write_worker(root, pid=os.getpid(), run_id=trace.run_id, launched_at=launched_at)
        pm.touch(project_id, status="RUNNING")
        trace.log(
            "project_context",
            project_id=project_id,
            project_uuid=info.project_uuid,
            title=info.title,
            experiment="Teorem Araştırması",
        )

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
    except Exception as exc:
        exit_code = 2
        result = f"Worker failed: {exc}"
        try:
            _mark_runtime_error(root, exc)
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
                trace.close()
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
