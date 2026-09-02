from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lab import Agent, TheoremResearchLab, Trace
from lab.prompts import ROLE_LIBRARY
from lab.research_state import ResearchState


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
)


class _InconclusiveLiterature:
    """Deterministic literature stub so the probe measures the LLM pipeline, not external search uptime."""

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


def summarize_probe(trace_path: Path, summary_path: Path) -> dict[str, Any]:
    events = _read_events(trace_path)
    run_summary = _read_json(summary_path)
    counts = {name: 0 for name in EVENT_COUNTERS}
    iterations: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    llm_calls: list[dict[str, Any]] = []

    for event in events:
        event_type = str(event.get("type") or "")
        if event_type in counts:
            counts[event_type] += 1
        if event_type == "iteration_end":
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
            tool_results.append(
                {
                    "step_key": event.get("step_key"),
                    "tool": event.get("tool"),
                    "ok": event.get("ok"),
                    "error": event.get("error"),
                    "metadata": event.get("metadata"),
                }
            )
        elif event_type == "llm_call":
            llm_calls.append(
                {
                    "agent": event.get("agent"),
                    "model": event.get("model"),
                    "prompt_tokens": event.get("prompt_tokens", 0),
                    "completion_tokens": event.get("completion_tokens", 0),
                    "reasoning_tokens": event.get("reasoning_tokens", 0),
                    "cached_tokens": event.get("cached_tokens", 0),
                    "total_tokens": event.get("total_tokens", 0),
                    "cost_usd": event.get("cost_usd"),
                    "latency_s": event.get("latency_s", 0.0),
                }
            )

    return {
        "run_id": run_summary.get("run_id"),
        "git_sha": _git_sha(),
        "finished_at": run_summary.get("finished_at"),
        "wall_time_s": run_summary.get("wall_time_s"),
        "total_calls": run_summary.get("total_calls", 0),
        "total_prompt_tokens": run_summary.get("total_prompt_tokens", 0),
        "total_completion_tokens": run_summary.get("total_completion_tokens", 0),
        "total_reasoning_tokens": run_summary.get("total_reasoning_tokens", 0),
        "total_cached_tokens": run_summary.get("total_cached_tokens", 0),
        "total_tokens": run_summary.get("total_tokens", 0),
        "total_cost_usd": run_summary.get("total_cost_usd", 0.0),
        "cost_complete": run_summary.get("cost_complete", False),
        "event_counts": counts,
        "iterations": iterations,
        "tool_results": tool_results,
        "llm_calls": llm_calls,
        "agent_totals": run_summary.get("agents", {}),
    }


def _markdown_report(report: dict[str, Any]) -> str:
    counts = report["event_counts"]
    lines = [
        "# ailab two-iteration baseline probe",
        "",
        f"- git SHA: `{report['git_sha']}`",
        f"- run ID: `{report['run_id']}`",
        f"- LLM calls: **{report['total_calls']}**",
        f"- total tokens: **{report['total_tokens']}**",
        f"- reported cost: **${float(report['total_cost_usd'] or 0.0):.6f}**",
        f"- wall time: **{report['wall_time_s']} s**",
        f"- JSON parse failures: **{counts['structured_output_parse_failed']}**",
        f"- JSON repairs completed: **{counts['structured_output_repaired']}**",
        f"- guard downgrades: **{counts['status_downgraded_by_guard']}**",
        f"- agent retries: **{counts['agent_retry']}**",
        "",
        "## Iterations",
    ]
    if report["iterations"]:
        for item in report["iterations"]:
            lines.append(
                f"- iteration {item['iteration']}: `{item['status']}` / `{item['decision']}` / "
                f"item `{item['item_id']}`"
            )
    else:
        lines.append("- no iteration_end event recorded")
    lines += ["", "## Tool results"]
    if report["tool_results"]:
        for item in report["tool_results"]:
            lines.append(
                f"- `{item['step_key']}`: `{item['tool']}` ok={item['ok']} error={item['error'] or '-'}"
            )
    else:
        lines.append("- no deterministic tool result recorded")
    lines += ["", "## Per-call usage"]
    for index, call in enumerate(report["llm_calls"], 1):
        lines.append(
            f"- {index}. `{call['agent']}` / `{call['model']}`: {call['total_tokens']} tokens, "
            f"cost={call['cost_usd']}, latency={call['latency_s']}s"
        )
    return "\n".join(lines) + "\n"


def _agent(role: str, model: str, max_tokens: int, reasoning_effort: str | None) -> Agent:
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
    max_tokens: int,
    reasoning_effort: str | None,
    out_dir: Path,
    problem: str,
) -> Path:
    load_dotenv()
    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY veya OPENAI_API_KEY gerekli.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    probe_root = out_dir / f"probe_{stamp}"
    state_root = probe_root / "research_state"
    runs_root = probe_root / "runs"
    probe_root.mkdir(parents=True, exist_ok=False)

    trace = Trace("baseline-probe", out_dir=runs_root)
    state = ResearchState(state_root)
    trace.log(
        "baseline_probe_config",
        git_sha=_git_sha(),
        model=model,
        iterations=iterations,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        problem=problem,
    )
    lab = TheoremResearchLab(trace, state, literature=_InconclusiveLiterature(), max_retries=2)
    agents = {
        role: _agent(role, model, max_tokens, reasoning_effort)
        for role in ROLE_TEMPERATURES
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a two-iteration real-LLM baseline through TheoremResearchLab.run()."
    )
    parser.add_argument("--model", default=os.environ.get("LAB_BASELINE_MODEL", "openai/gpt-4o-mini"))
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--reasoning-effort", default=os.environ.get("LAB_BASELINE_REASONING_EFFORT", "low"))
    parser.add_argument("--out-dir", type=Path, default=Path("baseline_runs"))
    parser.add_argument("--problem", default=TOY_PROBLEM)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be >= 1")
    if args.max_tokens < 128:
        raise SystemExit("--max-tokens must be >= 128")
    report_path = run_probe(
        model=args.model,
        iterations=args.iterations,
        max_tokens=args.max_tokens,
        reasoning_effort=args.reasoning_effort,
        out_dir=args.out_dir,
        problem=args.problem,
    )
    print(report_path)


if __name__ == "__main__":
    main()
