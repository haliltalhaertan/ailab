from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lab import TheoremResearchLab
from lab.agent import Agent
from lab.literature import LiteratureClient
from lab.prompts import ROLE_LIBRARY
from lab.research_state import ResearchState
from lab.trace_completion import Trace


TOY_PROBLEM = (
    "Baseline-only toy problem: for every integer n with 0 <= n <= 20, investigate whether n*(n+1) is even. "
    "This run exists to exercise the production research pipeline, not to claim novelty. Prefer the checked-in Z3 tool "
    "when deterministic checking is useful; avoid Lean and generated Python unless strictly necessary."
)

ROLE_TEMPERATURES = {
    "ResearchManager": 0.2,
    "Theorist": 0.4,
    "AdversarialCritic": 0.2,
    "VerificationEngineer": 0.1,
    "LiteratureScout": 0.1,
    "IndependentAuditor": 0.1,
}

EVENT_COUNTERS = (
    "structured_output_parse_failed",
    "structured_output_repaired",
    "structured_output_repair_failed",
    "status_downgraded_by_guard",
    "tool_result",
    "agent_error",
    "agent_retry",
    "step_reused",
    "unusually_expensive_call",
    "incomplete_output_not_promotable",
    "truncated_retry",
    "effort_coerced",
)


class _InconclusiveLiterature(LiteratureClient):
    """Deterministic stub so the probe measures the LLM pipeline, not literature-service uptime."""

    def search(self, query: str, limit: int = 8) -> list[Any]:
        del query, limit
        return []


def _git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip() or "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _read_events(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _preview(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _iteration_from_step(step_key: str) -> int | None:
    parts = str(step_key or "").split(":")
    if len(parts) < 2 or parts[0] != "iter":
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _bump(mapping: dict[str, int], value: Any) -> None:
    key = str(value or "unknown")
    mapping[key] = mapping.get(key, 0) + 1


def _agent_dict(agent_config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(agent_config, dict):
        return {}
    raw_agents = agent_config.get("agents", agent_config)
    if isinstance(raw_agents, dict):
        return {
            str(role): dict(raw)
            for role, raw in raw_agents.items()
            if isinstance(raw, dict)
        }
    if isinstance(raw_agents, list):
        result: dict[str, dict[str, Any]] = {}
        for raw in raw_agents:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or raw.get("display_role") or "").strip()
            if role:
                result[role] = dict(raw)
        return result
    raise ValueError("agent_config must be a worker-request agent object/list or contain an 'agents' field")


def resolve_agent_config(
    *,
    model: str,
    reasoning_effort: str | None,
    max_tokens: int | None,
    agent_config: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve a worker_request-style agent dictionary into probe role settings."""

    supplied = _agent_dict(agent_config)
    resolved: dict[str, dict[str, Any]] = {}
    for role in ROLE_TEMPERATURES:
        raw = supplied.get(role, {})
        role_model = str(raw.get("model") or model).strip()
        role_effort_value = raw.get("reasoning_effort", reasoning_effort)
        role_effort = None if role_effort_value in {None, "", "none"} else str(role_effort_value)
        raw_max = raw.get("max_tokens") if "max_tokens" in raw else max_tokens
        role_max_tokens = int(str(raw_max)) if raw_max not in {None, ""} else None
        if not role_model:
            raise ValueError(f"Missing model for baseline role {role}")
        if role_max_tokens is not None and role_max_tokens < 128:
            raise ValueError(f"max_tokens for {role} must be >= 128 when explicitly configured")
        resolved[role] = {
            "model": role_model,
            "reasoning_effort": role_effort,
            "max_tokens": role_max_tokens,
        }
    return resolved


def verified_progress_from_events(events: list[dict[str, Any]]) -> int:
    progress = 0
    for event in events:
        kind = str(event.get("type") or "")
        if kind == "tool_result" and bool(event.get("ok")):
            progress = max(progress, 1)
        elif kind == "tool_result_evidence" and str(event.get("evidence_kind") or "").upper() == "EXACT_PASS":
            progress = max(progress, 2)
        elif kind == "verified_progress_claim_match" and bool(event.get("ok", True)):
            progress = max(progress, 3)
        elif kind in {"target_transition_applied", "pilot_target_transition_applied"}:
            detail = event.get("detail") or {}
            status = str(event.get("status") or (detail.get("status") if isinstance(detail, dict) else "") or "").upper()
            if status == "CLOSED" or bool((detail.get("closed") if isinstance(detail, dict) else False)):
                progress = max(progress, 4)
        elif kind == "run_completed_all_targets_closed":
            progress = max(progress, 4)
    return progress


def summarize_probe(trace_path: Path, summary_path: Path) -> dict[str, Any]:
    events = _read_events(trace_path)
    run_summary = _read_json(summary_path)
    counts = {name: 0 for name in EVENT_COUNTERS}
    iterations: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    llm_calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    role_outputs: dict[str, list[dict[str, Any]]] = {}
    role_budget: dict[str, dict[str, Any]] = {}
    iteration_usage: dict[int, dict[str, Any]] = {}
    active_step_by_agent: dict[str, str] = {}
    probe_config: dict[str, Any] = {}
    retry_recovered = 0
    retry_failed = 0
    max_tokens_sources: dict[str, int] = {}
    catalog_sources: dict[str, int] = {}
    effort_resolutions: dict[str, int] = {}

    for event in events:
        event_type = str(event.get("type") or "")
        if event_type in counts:
            counts[event_type] += 1
        if event_type == "baseline_probe_config":
            probe_config = dict(event)
        elif event_type == "truncated_retry":
            outcome = str(event.get("outcome") or "")
            if outcome == "recovered":
                retry_recovered += 1
            elif outcome == "failed":
                retry_failed += 1
        elif event_type == "stage":
            agent = str(event.get("agent") or "")
            step_key = str(event.get("step_key") or "")
            if agent and step_key:
                active_step_by_agent[agent] = step_key
        elif event_type == "stage_end":
            agent = str(event.get("agent") or "")
            step_key = str(event.get("step_key") or "")
            if agent and active_step_by_agent.get(agent) == step_key:
                active_step_by_agent.pop(agent, None)
        elif event_type == "iteration_end":
            iterations.append(
                {
                    "iteration": event.get("iteration"),
                    "item_id": event.get("item_id"),
                    "decision": event.get("decision"),
                    "status": event.get("status"),
                    "next_task": event.get("next_task"),
                }
            )
        elif event_type == "tool_result":
            item = {
                "step_key": event.get("step_key"),
                "tool": event.get("tool"),
                "ok": event.get("ok"),
                "error": event.get("error"),
                "metadata": event.get("metadata"),
            }
            tool_results.append(item)
            if event.get("ok") is False:
                errors.append(
                    {
                        "type": "tool_result",
                        "step_key": event.get("step_key"),
                        "role": "",
                        "error": str(event.get("error") or "tool returned ok=false"),
                    }
                )
        elif event_type == "llm_call":
            agent = str(event.get("agent") or "Agent")
            step_key = active_step_by_agent.get(agent, "")
            budget = event.get("budget") or {}
            completion_tokens = int(event.get("completion_tokens", 0) or 0)
            reasoning_tokens = int(event.get("reasoning_tokens", 0) or 0)
            reasoning_ratio = reasoning_tokens / completion_tokens if completion_tokens > 0 else None
            call = {
                "agent": agent,
                "step_key": step_key,
                "model": event.get("model"),
                "prompt_tokens": event.get("prompt_tokens", 0),
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "reasoning_completion_ratio": reasoning_ratio,
                "answer_chars": int(event.get("answer_chars", len(str(event.get("output") or ""))) or 0),
                "cached_tokens": event.get("cached_tokens", 0),
                "total_tokens": event.get("total_tokens", 0),
                "cost_usd": event.get("cost_usd"),
                "latency_s": event.get("latency_s", 0.0),
                "finish_reason": event.get("finish_reason"),
                "truncated": bool(event.get("truncated")),
                "requested_max_tokens": event.get("requested_max_tokens"),
                "model_max_completion_tokens": event.get("model_max_completion_tokens"),
                "max_tokens_source": event.get("max_tokens_source"),
                "catalog_source": event.get("catalog_source"),
                "reasoning_effort_requested": event.get("reasoning_effort_requested"),
                "reasoning_effort_sent": event.get("reasoning_effort_sent"),
                "effort_resolution": event.get("effort_resolution"),
                "reasoning_max_tokens_sent": event.get("reasoning_max_tokens_sent"),
                "budget": budget,
                "output_preview": _preview(event.get("output")),
            }
            llm_calls.append(call)
            _bump(max_tokens_sources, call["max_tokens_source"])
            _bump(catalog_sources, call["catalog_source"])
            _bump(effort_resolutions, call["effort_resolution"])
            role_outputs.setdefault(agent, []).append(
                {
                    "step_key": step_key,
                    "model": event.get("model"),
                    "output_preview": call["output_preview"],
                }
            )
            role_row = role_budget.setdefault(
                agent,
                {
                    "calls": 0,
                    "actual_tokens": 0,
                    "expected_min": None,
                    "expected_max": None,
                    "over_budget_calls": 0,
                    "truncated_calls": 0,
                },
            )
            role_row["calls"] += 1
            role_row["actual_tokens"] += int(event.get("total_tokens", 0) or 0)
            if budget.get("expected_min") is not None:
                role_row["expected_min"] = int(budget["expected_min"])
            if budget.get("expected_max") is not None:
                role_row["expected_max"] = int(budget["expected_max"])
            if budget.get("over_budget"):
                role_row["over_budget_calls"] += 1
            if event.get("truncated"):
                role_row["truncated_calls"] += 1
            iteration = _iteration_from_step(step_key)
            if iteration is not None:
                usage = iteration_usage.setdefault(
                    iteration,
                    {
                        "iteration": iteration,
                        "calls": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                        "cost_available_calls": 0,
                        "llm_latency_s": 0.0,
                        "roles": [],
                    },
                )
                usage["calls"] += 1
                usage["total_tokens"] += int(event.get("total_tokens", 0) or 0)
                usage["llm_latency_s"] += float(event.get("latency_s", 0.0) or 0.0)
                if event.get("cost_usd") is not None:
                    usage["cost_usd"] += float(event["cost_usd"])
                    usage["cost_available_calls"] += 1
                if agent not in usage["roles"]:
                    usage["roles"].append(agent)
        elif event_type in {"agent_error", "structured_output_repair_failed", "baseline_probe_exception"}:
            errors.append(
                {
                    "type": event_type,
                    "step_key": event.get("step_key"),
                    "role": event.get("agent"),
                    "error": str(event.get("error") or "unknown error"),
                }
            )

    per_iteration = []
    for iteration in sorted(iteration_usage):
        usage = iteration_usage[iteration]
        usage["cost_usd"] = round(float(usage["cost_usd"]), 8)
        usage["llm_latency_s"] = round(float(usage["llm_latency_s"]), 3)
        usage["cost_complete"] = usage["cost_available_calls"] == usage["calls"]
        per_iteration.append(usage)

    return {
        "run_id": run_summary.get("run_id"),
        "git_sha": _git_sha(),
        "finished_at": run_summary.get("finished_at"),
        "wall_time_s": run_summary.get("wall_time_s"),
        "requested_iterations": int(probe_config.get("iterations", 0) or 0),
        "completed_iterations": len(iterations),
        "agent_config": probe_config.get("agent_config", {}),
        "total_calls": run_summary.get("total_calls", 0),
        "total_prompt_tokens": run_summary.get("total_prompt_tokens", 0),
        "total_completion_tokens": run_summary.get("total_completion_tokens", 0),
        "total_reasoning_tokens": run_summary.get("total_reasoning_tokens", 0),
        "total_cached_tokens": run_summary.get("total_cached_tokens", 0),
        "total_tokens": run_summary.get("total_tokens", 0),
        "total_cost_usd": run_summary.get("total_cost_usd", 0.0),
        "cost_complete": run_summary.get("cost_complete", False),
        "event_counts": counts,
        "retry_recovered": retry_recovered,
        "retry_failed": retry_failed,
        "max_tokens_source_distribution": max_tokens_sources,
        "catalog_source_distribution": catalog_sources,
        "effort_resolution_distribution": effort_resolutions,
        "verified_progress": verified_progress_from_events(events),
        "iterations": iterations,
        "per_iteration": per_iteration,
        "tool_results": tool_results,
        "llm_calls": llm_calls,
        "role_outputs": role_outputs,
        "role_budget": role_budget,
        "errors": errors,
        "agent_totals": run_summary.get("agents", {}),
    }


def _markdown_report(report: dict[str, Any]) -> str:
    counts = report["event_counts"]
    requested = int(report.get("requested_iterations", 0) or 0)
    completed = int(report.get("completed_iterations", len(report.get("iterations", []))) or 0)
    lines = [
        "# ailab baseline probe",
        "",
        f"- git SHA: `{report['git_sha']}`",
        f"- run ID: `{report['run_id']}`",
        f"- requested/completed iterations: **{requested}/{completed}**",
        f"- verified progress: **{int(report.get('verified_progress', 0))}/4**",
        f"- LLM calls: **{report['total_calls']}**",
        f"- total tokens: **{report['total_tokens']}**",
        f"- reported cost: **${float(report['total_cost_usd'] or 0.0):.6f}**",
        f"- wall time: **{report['wall_time_s']} s**",
        f"- JSON parse failures: **{counts['structured_output_parse_failed']}**",
        f"- JSON repairs completed: **{counts['structured_output_repaired']}**",
        f"- JSON repair failures: **{counts['structured_output_repair_failed']}**",
        f"- incomplete outputs blocked from promotion: **{counts.get('incomplete_output_not_promotable', 0)}**",
        f"- truncated calls: **{sum(int(row.get('truncated_calls', 0)) for row in report.get('role_budget', {}).values())}**",
        f"- truncation retries recovered/failed: **{report.get('retry_recovered', 0)}/{report.get('retry_failed', 0)}**",
        f"- effort coercion events: **{counts.get('effort_coerced', 0)}**",
        f"- unusually expensive calls: **{counts.get('unusually_expensive_call', 0)}**",
        f"- guard downgrades: **{counts['status_downgraded_by_guard']}**",
        f"- agent retries: **{counts['agent_retry']}**",
        "",
        "## Completion policy distributions",
        f"- max_tokens_source: `{json.dumps(report.get('max_tokens_source_distribution', {}), sort_keys=True)}`",
        f"- catalog_source: `{json.dumps(report.get('catalog_source_distribution', {}), sort_keys=True)}`",
        f"- effort_resolution: `{json.dumps(report.get('effort_resolution_distribution', {}), sort_keys=True)}`",
        "",
        "## Agent configuration",
    ]
    agent_config = report.get("agent_config") or {}
    if agent_config:
        for role, spec in agent_config.items():
            if not isinstance(spec, dict):
                continue
            max_label = spec.get("max_tokens") if spec.get("max_tokens") is not None else "catalog/provider capacity (no role cap)"
            lines.append(
                f"- `{role}`: `{spec.get('model')}` / effort=`{spec.get('reasoning_effort') or 'provider-default'}` / "
                f"max_tokens={max_label}"
            )
    else:
        lines.append("- configuration was not recorded")

    lines += ["", "## Passive token telemetry"]
    if report.get("role_budget"):
        for role, row in report["role_budget"].items():
            minimum = row.get("expected_min")
            maximum = row.get("expected_max")
            expected = f"{minimum}–{maximum}" if minimum is not None or maximum is not None else "N/A (not calibrated)"
            lines.append(
                f"- `{role}`: expected={expected}; actual_total={row.get('actual_tokens', 0)}; "
                f"exceed_calls={row.get('over_budget_calls', 0)}; truncated_calls={row.get('truncated_calls', 0)}"
            )
    else:
        lines.append("- no LLM calls recorded")

    lines += ["", "## Per-iteration usage"]
    if report.get("per_iteration"):
        for usage in report["per_iteration"]:
            cost = float(usage.get("cost_usd", 0.0) or 0.0)
            cost_suffix = "" if usage.get("cost_complete") else " (partial provider cost data)"
            lines.append(
                f"- iteration {usage['iteration']}: **{usage['total_tokens']} tokens**, "
                f"**${cost:.6f}**, **{usage['llm_latency_s']} s LLM latency**, "
                f"calls={usage['calls']}{cost_suffix}"
            )
    else:
        lines.append("- no iteration-attributed LLM calls recorded")

    lines += ["", "## Iterations"]
    if report["iterations"]:
        for item in report["iterations"]:
            lines.append(
                f"- iteration {item['iteration']}: `{item['status']}` / `{item['decision']}` / "
                f"item `{item['item_id']}`"
            )
    else:
        lines.append("- no iteration_end event recorded")

    lines += ["", "## Role outputs"]
    if report.get("role_outputs"):
        for role, outputs in report["role_outputs"].items():
            lines.append(f"- **{role}**")
            for output in outputs:
                step = output.get("step_key") or "unattributed"
                preview = output.get("output_preview") or "(empty output)"
                lines.append(f"  - `{step}`: {preview}")
    else:
        lines.append("- no LLM output recorded")

    lines += ["", "## Tool results"]
    if report["tool_results"]:
        for item in report["tool_results"]:
            lines.append(
                f"- `{item['step_key']}`: `{item['tool']}` ok={item['ok']} error={item['error'] or '-'}"
            )
    else:
        lines.append("- no deterministic tool result recorded")

    lines += ["", "## Errors"]
    if report.get("errors"):
        for error in report["errors"]:
            lines.append(
                f"- `{error.get('type')}` / `{error.get('role') or '-'}` / "
                f"`{error.get('step_key') or '-'}`: {error.get('error') or '-'}"
            )
    else:
        lines.append("- none recorded")

    lines += ["", "## Per-call usage"]
    for index, call in enumerate(report["llm_calls"], 1):
        ratio = call.get("reasoning_completion_ratio")
        ratio_label = "N/A" if ratio is None else f"{float(ratio):.4f}"
        lines.append(
            f"- {index}. `{call['agent']}` / `{call['model']}` / `{call.get('step_key') or '-'}`: "
            f"completion={call['completion_tokens']}, reasoning={call['reasoning_tokens']}, answer_chars={call['answer_chars']}, "
            f"ratio={ratio_label}, total={call['total_tokens']}, cost={call['cost_usd']}, latency={call['latency_s']}s, "
            f"finish={call.get('finish_reason')}, truncated={call.get('truncated')}, "
            f"requested_max={call.get('requested_max_tokens')}, model_max={call.get('model_max_completion_tokens')}, "
            f"max_source={call.get('max_tokens_source')}, catalog_source={call.get('catalog_source')}, "
            f"effort={call.get('reasoning_effort_requested')}→{call.get('reasoning_effort_sent')} ({call.get('effort_resolution')})"
        )
    return "\n".join(lines) + "\n"


def _agent(role: str, model: str, max_tokens: int | None, reasoning_effort: str | None) -> Agent:
    return Agent(
        name=role,
        system_prompt=ROLE_LIBRARY[role],
        model=model,
        temperature=ROLE_TEMPERATURES[role],
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )


def run_probe(
    *,
    model: str,
    iterations: int,
    max_tokens: int | None,
    reasoning_effort: str | None,
    out_dir: Path,
    problem: str,
    agent_config: dict[str, Any] | None = None,
) -> Path:
    load_dotenv()
    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY veya OPENAI_API_KEY gerekli.")

    resolved_config = resolve_agent_config(
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        agent_config=agent_config,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    probe_root = out_dir / f"probe_{stamp}"
    state_root = probe_root / "research_state"
    runs_root = probe_root / "runs"
    probe_root.mkdir(parents=True, exist_ok=False)

    trace = Trace("baseline-probe", out_dir=runs_root)
    trace.configure_theorem_stages(
        iterations=iterations,
        checkpoint_every=0,
        has_literature_agent=True,
    )
    state = ResearchState(state_root)
    trace.log(
        "baseline_probe_config",
        git_sha=_git_sha(),
        model=model,
        iterations=iterations,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        agent_config=resolved_config,
        problem=problem,
    )
    lab = TheoremResearchLab(trace, state, literature=_InconclusiveLiterature(), max_retries=2)
    agents = {
        role: _agent(
            role,
            str(spec["model"]),
            int(spec["max_tokens"]) if spec.get("max_tokens") is not None else None,
            spec.get("reasoning_effort"),
        )
        for role, spec in resolved_config.items()
    }

    run_error = ""
    try:
        result = lab.run(
            problem,
            manager=agents["ResearchManager"],
            proposer=agents["Theorist"],
            critic=agents["AdversarialCritic"],
            verifier=agents["VerificationEngineer"],
            literature_agent=agents["LiteratureScout"],
            auditor=agents["IndependentAuditor"],
            iterations=iterations,
            checkpoint_every=0,
        )
        (probe_root / "result.md").write_text(result, encoding="utf-8")
    except Exception as exc:
        run_error = repr(exc)
        trace.log("baseline_probe_exception", error=run_error)
        (probe_root / "result.md").write_text(f"# Baseline probe failed\n\n{run_error}\n", encoding="utf-8")
    finally:
        summary_path = trace.close()

    report = summarize_probe(trace.path, summary_path)
    report["run_error"] = run_error
    report_path = probe_root / "baseline_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (probe_root / "baseline_report.md").write_text(_markdown_report(report), encoding="utf-8")
    if run_error:
        raise RuntimeError(f"Baseline probe failed; report preserved at {report_path}: {run_error}")
    return report_path


def _load_agent_config(value: str | None) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        parsed = json.loads(path.read_text(encoding="utf-8"))
    else:
        parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("--agent-config must resolve to a JSON object")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a real-LLM baseline through TheoremResearchLab.run()."
    )
    parser.add_argument("--model", default=os.environ.get("LAB_BASELINE_MODEL", "openai/gpt-4o-mini"))
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Optional explicit cap for a special probe. Default: use catalog model capacity when known; LAB_EMERGENCY_MAX_TOKENS remains a separate global safety ceiling.",
    )
    parser.add_argument("--reasoning-effort", default=os.environ.get("LAB_BASELINE_REASONING_EFFORT", "low"))
    parser.add_argument(
        "--agent-config",
        help=(
            "Worker-request-style agent JSON object, full worker_request JSON, or path to either. "
            "Per-role model/reasoning_effort and optional max_tokens override the global defaults."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("baseline_runs"))
    parser.add_argument("--problem", default=TOY_PROBLEM)
    parser.add_argument(
        "--report-copy",
        type=Path,
        help="Optional checked-in destination for a copy of baseline_report.md (for example docs/baselines/run.md).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be >= 1")
    if args.max_tokens is not None and args.max_tokens < 128:
        raise SystemExit("--max-tokens must be >= 128 when explicitly configured")
    agent_config = _load_agent_config(args.agent_config)
    report_path = run_probe(
        model=args.model,
        iterations=args.iterations,
        max_tokens=args.max_tokens,
        reasoning_effort=args.reasoning_effort,
        out_dir=args.out_dir,
        problem=args.problem,
        agent_config=agent_config,
    )
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report_path.with_name("baseline_report.md"), args.report_copy)
        print(args.report_copy)
    print(report_path)
