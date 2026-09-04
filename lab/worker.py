from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from lab import Orchestrator, TheoremResearchLab
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
from lab.run_controller import ResearchStopped, RunController
from lab.tool_registry import EFFECTIVE_AVAILABILITY_ENV, ToolRegistry
from lab.tools import ToolResult
from lab.trace import Trace
from lab.worker_runtime import WorkerRuntimeBridge


AgentFactory = Callable[[str, dict[str, Any]], Agent]
EXPERIMENT_METHODS = {"theorem_lab", "research_loop", "debate", "pipeline", "panel"}
TERMINAL_RUNTIME_STATUSES = {"COMPLETED", "STOPPED", "PAUSED_ERROR", "INTERRUPTED"}
RESUMABLE_TOOL_SNAPSHOT_STATUSES = {"RUNNING", "STOPPED", "PAUSED_ERROR", "INTERRUPTED"}
TOOL_AVAILABILITY_FILE = "tool_availability.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    value = read_json_tolerant(path, None)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {path}")
    return value


def _agent(role: str, raw: dict[str, Any]) -> Agent:
    return Agent(
        name=str(raw.get("display_role") or raw.get("name") or role),
        system_prompt=str(raw.get("system_prompt") or raw.get("prompt") or ""),
        model=str(raw.get("model") or ""),
        temperature=float(raw.get("temperature", raw.get("temp", 0.2))),
        max_tokens=raw.get("max_tokens"),
        reasoning_effort=raw.get("reasoning_effort"),
    )


def _write_worker(root: Path, *, pid: int, run_id: str, launched_at: str) -> None:
    atomic_write_json(
        root / "worker.json",
        {"pid": int(pid), "run_id": str(run_id), "launched_at": str(launched_at)},
    )


def _mark_runtime_error(root: Path, exc: Exception) -> None:
    path = root / "runtime.json"
    current = read_json_tolerant(path, {})
    current = dict(current) if isinstance(current, dict) else {}
    now = _now()
    current.update(
        {"status": "PAUSED_ERROR", "last_error": repr(exc), "pid": os.getpid(), "updated_at": now, "heartbeat_at": now}
    )
    atomic_write_json(path, current)


def _availability_row(available: bool, reason: str) -> dict[str, Any]:
    return {"available": bool(available), "reason": str(reason)}


def _availability_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if isinstance(raw, dict):
            rows[str(name)] = dict(raw)
    return rows


class _UnavailableLeanTool:
    def __init__(self, reason: str):
        self.reason = str(reason)

    def _result(self) -> ToolResult:
        return ToolResult(
            False,
            "lean",
            error=f"Tool bu koşuda kullanılamıyor: {self.reason}",
            metadata={"tool_unavailable": True, "availability_reason": self.reason},
        )

    def draft_source(self, *_args: Any, **_kwargs: Any) -> ToolResult:
        return self._result()

    def check_file(self, *_args: Any, **_kwargs: Any) -> ToolResult:
        return self._result()


class _UnavailableCodeRunner:
    def __init__(self, reason: str):
        self.reason = str(reason)

    def run(self, **_kwargs: Any) -> ToolResult:
        return ToolResult(
            False,
            "code_experiment",
            error=f"Tool bu koşuda kullanılamıyor: {self.reason}",
            metadata={
                "tool_unavailable": True,
                "availability_reason": self.reason,
                "evidence_level": "COMPUTATION_ONLY",
            },
        )


def _bind_special_tool_guards(lab: Any, snapshot: dict[str, Any]) -> None:
    """Guard theorem-engine special dispatch paths with the effective snapshot.

    ``lean_draft`` and ``code_experiment`` have engine-specific execution paths
    rather than ordinary registry dispatch. Bind fail-closed stand-ins when the
    run-scoped effective universe marks either capability closed.
    """

    effective = _availability_map(snapshot.get("effective_tool_availability"))

    lean_row = effective.get("lean_draft", {})
    if not bool(lean_row.get("available")):
        toolbox = getattr(lab, "toolbox", None)
        if toolbox is not None and hasattr(toolbox, "lean"):
            setattr(
                toolbox,
                "lean",
                _UnavailableLeanTool(str(lean_row.get("reason") or "Lean bu run'da kapalı")),
            )

    code_row = effective.get("code_experiment", {})
    if not bool(code_row.get("available")) and hasattr(lab, "code_runner"):
        setattr(
            lab,
            "code_runner",
            _UnavailableCodeRunner(str(code_row.get("reason") or "Container bu run'da kapalı")),
        )


def _tool_availability_for_run(
    root: Path,
    lab: Any,
    runtime_before: dict[str, Any],
    *,
    resume_requested: bool = False,
) -> dict[str, Any]:
    """Freeze run capabilities and only allow explicit resume-time narrowing.

    A previous capability snapshot is reused only when the worker request is an
    explicit resume and the previous runtime state is resumable. Merely starting
    a new run from a STOPPED/INTERRUPTED project always measures a fresh declared
    universe. A tool that disappears on a true resume is removed immediately.
    """

    registry = getattr(lab, "registry", None)
    availability_fn = getattr(registry, "availability", None)
    if callable(availability_fn):
        runtime = _availability_map(availability_fn())
    else:
        runtime = ToolRegistry().availability()

    workspace = getattr(lab, "code_workspace", None)
    if workspace is None:
        runtime["code_experiment"] = _availability_row(False, "code workspace bu runner'da tanımlı değil")
    else:
        execution_available = bool(getattr(workspace, "execution_available", False))
        engine = str(getattr(workspace, "container_engine", "") or "")
        runtime["code_experiment"] = _availability_row(
            execution_available,
            f"container engine kullanılabilir: {engine}" if execution_available else "container engine kullanılamıyor",
        )

    path = root / TOOL_AVAILABILITY_FILE
    previous = read_json_tolerant(path, {})
    previous = dict(previous) if isinstance(previous, dict) else {}
    previous_declared = previous.get("declared_tool_availability")
    status_before = str(runtime_before.get("status") or "NEW").upper()
    resume = bool(
        resume_requested
        and status_before in RESUMABLE_TOOL_SNAPSHOT_STATUSES
        and isinstance(previous_declared, dict)
    )
    declared = _availability_map(previous_declared) if resume else {name: dict(raw) for name, raw in runtime.items()}

    effective: dict[str, dict[str, Any]] = {}
    for name in sorted(set(declared) | set(runtime)):
        drow = declared.get(name, {})
        rrow = runtime.get(name, {})
        declared_open = bool(drow.get("available"))
        runtime_open = bool(rrow.get("available"))
        if not declared_open:
            reason = f"run başında kapalı: {drow.get('reason') or 'capability yok'}"
            effective[name] = _availability_row(False, reason)
        elif not runtime_open:
            reason = f"runtime daralması: {rrow.get('reason') or 'capability kayboldu'}"
            effective[name] = _availability_row(False, reason)
        else:
            effective[name] = _availability_row(True, str(rrow.get("reason") or drow.get("reason") or "available"))

    snapshot = {
        "availability_version": 1,
        "declared_tool_availability": declared,
        "runtime_tool_availability": runtime,
        "effective_tool_availability": effective,
        "resume_requested": bool(resume_requested),
        "resumed_snapshot": bool(resume),
        "captured_at": _now(),
    }
    atomic_write_json(path, snapshot)
    set_effective = getattr(registry, "set_effective_availability", None)
    if callable(set_effective):
        set_effective(effective)
    return snapshot


def _persist_tool_availability_in_run_config(root: Path, snapshot: dict[str, Any]) -> None:
    path = root / "run_config.json"
    raw = read_json_tolerant(path, {})
    if not isinstance(raw, dict) or not raw:
        return
    config = dict(raw)
    config["config_version"] = max(4, int(config.get("config_version", 0) or 0))
    for key in (
        "declared_tool_availability",
        "runtime_tool_availability",
        "effective_tool_availability",
    ):
        config[key] = snapshot.get(key, {})
    atomic_write_json(path, config)


def _trace_tool_availability(trace: Trace, snapshot: dict[str, Any]) -> None:
    declared = _availability_map(snapshot.get("declared_tool_availability"))
    runtime = _availability_map(snapshot.get("runtime_tool_availability"))
    effective = _availability_map(snapshot.get("effective_tool_availability"))
    trace.log(
        "tool_availability",
        declared_tool_availability=declared,
        runtime_tool_availability=runtime,
        effective_tool_availability=effective,
        resume_requested=bool(snapshot.get("resume_requested")),
        resumed_snapshot=bool(snapshot.get("resumed_snapshot")),
    )
    for name, raw in declared.items():
        if not raw.get("available"):
            continue
        current = runtime.get(name, {})
        if not current.get("available"):
            trace.log(
                "tool_availability_narrowed",
                tool=name,
                declared=raw,
                runtime=current,
                effective=effective.get(name),
            )
    for name, raw in runtime.items():
        if not raw.get("available"):
            continue
        original = declared.get(name, {})
        if original and not original.get("available"):
            trace.log(
                "tool_availability_not_widened",
                tool=name,
                declared=original,
                runtime=raw,
                effective=effective.get(name),
                reason="Yeni capability aynı resumable run içinde sessizce açılamaz; yeni run gerekir.",
            )


def _theorem_agents(raw_agents: Any, agent_factory: AgentFactory) -> dict[str, Agent]:
    if isinstance(raw_agents, dict):
        specs = [(str(role), raw) for role, raw in raw_agents.items() if isinstance(raw, dict)]
    elif isinstance(raw_agents, list):
        specs = []
        for raw in raw_agents:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or raw.get("display_role") or "")
            if role:
                specs.append((role, raw))
    else:
        raise ValueError("worker request agents must be an object or ordered list")
    agents = {role: agent_factory(role, raw) for role, raw in specs}
    required = {"ResearchManager", "Theorist", "AdversarialCritic", "VerificationEngineer", "IndependentAuditor"}
    missing = required - set(agents)
    if missing:
        raise ValueError(f"Missing worker agents: {sorted(missing)}")
    return agents


def _ordered_agents(raw_agents: Any, agent_factory: AgentFactory) -> list[Agent]:
    if not isinstance(raw_agents, list):
        raise ValueError("non-theorem worker request agents must be an ordered list")
    agents: list[Agent] = []
    for raw in raw_agents:
        if not isinstance(raw, dict):
            raise ValueError("worker request agent entry must be an object")
        role = str(raw.get("role") or raw.get("display_role") or "Agent")
        agents.append(agent_factory(role, raw))
    return agents


def _optional_agents(raw_optional: Any, agent_factory: AgentFactory) -> dict[str, Agent]:
    if raw_optional is None:
        return {}
    if not isinstance(raw_optional, dict):
        raise ValueError("worker request optional_agents must be an object")
    agents: dict[str, Agent] = {}
    for key, raw in raw_optional.items():
        if not isinstance(raw, dict):
            raise ValueError("optional agent entry must be an object")
        role = str(raw.get("role") or key)
        agent = agent_factory(role, raw)
        agents[str(key)] = agent
        agents.setdefault(role, agent)
    return agents


def _run_orchestrator(method: str, request: dict[str, Any], trace: Trace, agent_factory: AgentFactory, bridge: WorkerRuntimeBridge) -> str:
    prompt = str(request.get("prompt") or request.get("problem") or "")
    param = int(request.get("param", request.get("iterations", 0)) or 0)
    agents = _ordered_agents(request.get("agents"), agent_factory)
    optional = _optional_agents(request.get("optional_agents"), agent_factory)
    orchestrator = Orchestrator(trace, cancel_check=bridge.cancel_check, on_stage=bridge.on_stage)
    if method == "research_loop":
        if len(agents) < 2:
            raise ValueError("research_loop requires proposer and critic")
        return orchestrator.research_loop(prompt, agents[0], agents[1], iterations=max(1, param), synthesizer=optional.get("Raporcu"))
    if method == "debate":
        if not agents:
            raise ValueError("debate requires at least one debater")
        return orchestrator.debate(prompt, agents, rounds=max(1, param), judge=optional.get("Hakem"))
    if method == "pipeline":
        if not agents:
            raise ValueError("pipeline requires at least one agent")
        return orchestrator.pipeline(prompt, agents)
    if method == "panel":
        if not agents:
            raise ValueError("panel requires at least one panelist")
        return orchestrator.panel(prompt, agents, synthesizer=optional.get("Sentezleyici"))
    raise ValueError(f"Unsupported experiment_method: {method}")


def run_project(project_id: str, *, agent_factory: AgentFactory = _agent) -> int:
    load_dotenv()
    pm = ProjectManager()
    root = pm.project_root(project_id)
    lock = ProjectRunLock(root)
    try:
        lock.acquire()
    except ProjectBusyError as exc:
        try:
            atomic_write_json(root / "worker_busy.json", {"pid": os.getpid(), "rejected_at": _now(), "error": str(exc)})
        except Exception:
            pass
        return 3

    trace: Trace | None = None
    controller: RunController | None = None
    bridge: WorkerRuntimeBridge | None = None
    result = ""
    exit_code = 0
    final_status: str | None = None
    tool_snapshot: dict[str, Any] | None = None
    previous_effective_env = os.environ.get(EFFECTIVE_AVAILABILITY_ENV)
    try:
        info = pm.get(project_id)
        request = _read(root / "worker_request.json")
        runtime_prelaunch_raw = read_json_tolerant(root / "runtime.json", {})
        runtime_prelaunch = dict(runtime_prelaunch_raw) if isinstance(runtime_prelaunch_raw, dict) else {}
        if str(request.get("project_uuid") or "") != info.project_uuid:
            raise RuntimeError("worker request project_uuid does not match current project identity")
        method = str(request.get("experiment_method") or "theorem_lab")
        if method not in EXPERIMENT_METHODS:
            raise ValueError(f"Unsupported experiment_method: {method}")
        experiment_name = str(request.get("experiment_name") or ("Teorem Araştırması" if method == "theorem_lab" else method))

        trace = Trace(f"worker-{method}")
        _write_worker(root, pid=os.getpid(), run_id=trace.run_id, launched_at=_now())
        pm.touch(project_id, experiment=experiment_name, status="RUNNING")
        trace.log(
            "project_context",
            project_id=project_id,
            project_uuid=info.project_uuid,
            title=info.title,
            experiment=experiment_name,
            experiment_method=method,
        )

        if method == "theorem_lab":
            agents = _theorem_agents(request.get("agents") or {}, agent_factory)
            theorem_iterations = int(request.get("iterations", request.get("param", 5)))
            theorem_checkpoint_every = int(request.get("checkpoint_every", 2))
            trace.configure_theorem_stages(
                iterations=theorem_iterations,
                checkpoint_every=theorem_checkpoint_every,
                has_literature_agent="LiteratureScout" in agents,
            )
            runtime_before = _read(root / "runtime.json")
            trace.configure_theorem_resume_offset(
                completed_iterations=int(runtime_before.get("completed_iterations", 0) or 0),
                checkpoint_every=theorem_checkpoint_every,
            )
            state = ResearchState(root)
            lab = TheoremResearchLab(
                trace,
                state,
                code_experiment_settings_override=request.get("code_experiment") if isinstance(request.get("code_experiment"), dict) else None,
            )
            tool_snapshot = _tool_availability_for_run(
                root,
                lab,
                runtime_prelaunch,
                resume_requested=bool(request.get("resume")),
            )
            _trace_tool_availability(trace, tool_snapshot)
            _bind_special_tool_guards(lab, tool_snapshot)
            os.environ[EFFECTIVE_AVAILABILITY_ENV] = json.dumps(
                tool_snapshot["effective_tool_availability"],
                ensure_ascii=False,
                sort_keys=True,
            )
            lab.controller.lock = lock
            bridge = WorkerRuntimeBridge(lab.controller, background_heartbeat=True)
            trace.set_stage_listener(bridge.on_stage)
            result = lab.run(
                str(request.get("problem") or request.get("prompt") or ""),
                manager=agents["ResearchManager"],
                proposer=agents["Theorist"],
                code_agent=agents.get("CodeExperimentAgent"),
                critic=agents["AdversarialCritic"],
                verifier=agents["VerificationEngineer"],
                literature_agent=agents.get("LiteratureScout"),
                auditor=agents["IndependentAuditor"],
                iterations=theorem_iterations,
                literature_query=request.get("literature_query"),
                checkpoint_every=theorem_checkpoint_every,
            )
            _persist_tool_availability_in_run_config(root, tool_snapshot)
            theorem_status = str(lab.controller.runtime().get("status") or "COMPLETED").upper()
            if theorem_status not in TERMINAL_RUNTIME_STATUSES:
                theorem_status = "COMPLETED"
            bridge.close()
            bridge.set_runtime(status=theorem_status, current_agent="")
            final_status = theorem_status
        else:
            controller = RunController(root, trace)
            controller.lock = lock
            controller.clear_stale_stop()
            bridge = WorkerRuntimeBridge(controller, background_heartbeat=True)
            bridge.set_runtime(status="RUNNING", current_step="Deney başlatılıyor", current_agent="", last_error="")
            controller.check_stop()
            result = _run_orchestrator(method, request, trace, agent_factory, bridge)
            bridge.close()
            bridge.set_runtime(status="COMPLETED", current_step="Tamamlandı", current_agent="", last_error="")
            final_status = "COMPLETED"
    except ResearchStopped as exc:
        exit_code = 0
        result = f"Worker stopped: {exc}"
        final_status = "STOPPED"
        if bridge is not None:
            bridge.close()
            bridge.set_runtime(status="STOPPED", current_step="Durduruldu", current_agent="", last_error="")
        elif controller is not None:
            controller.set_runtime(status="STOPPED", current_step="Durduruldu", current_agent="", last_error="")
        if trace is not None:
            trace.log("run_stopped", reason=str(exc))
    except Exception as exc:
        exit_code = 2
        result = f"Worker failed: {exc}"
        final_status = "PAUSED_ERROR"
        if bridge is not None:
            bridge.close()
        try:
            _mark_runtime_error(root, exc)
        except Exception:
            pass
        if trace is not None:
            trace.log("worker_error", error=repr(exc))
    finally:
        if tool_snapshot is not None:
            try:
                _persist_tool_availability_in_run_config(root, tool_snapshot)
            except Exception:
                pass
        if previous_effective_env is None:
            os.environ.pop(EFFECTIVE_AVAILABILITY_ENV, None)
        else:
            os.environ[EFFECTIVE_AVAILABILITY_ENV] = previous_effective_env
        if bridge is not None:
            bridge.close()
        if trace is not None:
            trace.set_stage_listener(None)
        try:
            atomic_write_text(root / "worker_result.md", result)
        except Exception:
            pass
        if trace is not None and not trace.closed:
            try:
                trace.close()
            except Exception:
                pass
        if trace is not None and trace.closed:
            try:
                trace.compress_stream()
            except Exception:
                pass
        if final_status is not None:
            try:
                pm.touch(project_id, status=final_status)
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
