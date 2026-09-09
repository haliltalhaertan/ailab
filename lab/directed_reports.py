"""Read-only operational research summaries; never a mathematical truth verifier."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _object(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > 16_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _excerpt(value: Any, limit: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def build_research_report(run_folder: str | Path) -> dict[str, Any]:
    """Read existing contract, runtime and declared lane results without altering them.

    Completion is operational, scientific usefulness remains explicitly unreviewed.
    Reported cost bounds are declared reservation estimates, not billing guarantees.
    """
    root = Path(run_folder).resolve()
    contract = _object(root / "TASK_CONTRACT.json")
    runtime = _object(root / "runtime.json")
    usage = _dict(runtime.get("usage"))
    budgets = _dict(usage.get("lanes"))
    plan = _dict(contract.get("agent_plan"))
    declared = _list(plan.get("lanes"))
    statuses = _dict(runtime.get("lanes"))
    rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for lane in declared:
        if not isinstance(lane, dict):
            continue
        lane_id = lane.get("lane_id")
        if not isinstance(lane_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", lane_id):
            continue
        lane_root = root / "lanes" / lane_id
        if not lane_root.resolve().is_relative_to(root):
            continue
        result = _object(lane_root / "RESULT.json")
        budget = _dict(budgets.get(lane_id))
        result_usage = _dict(result.get("usage"))
        is_model = lane.get("execution", "model") == "model"
        calls = _number(budget.get("calls"))
        attempts = result.get("provider_attempts")
        attempted = bool(calls or (isinstance(attempts, list) and attempts))
        status = result.get("status") or statuses.get(lane_id, "PENDING")
        # Legacy results may lack an attempt journal; blocked dependencies are not attempts.
        if is_model and status in {"COMPLETED", "PARTIAL", "TIMEOUT"}:
            attempted = True
        answer = _excerpt(result.get("public_findings"))
        completed = status == "COMPLETED" and bool(answer)
        known = _number(budget.get("provider_cost_usd"))
        complete = budget.get("provider_cost_complete") is True
        upper = _number(budget.get("provider_cost_upper_bound_usd"))
        if known is None:
            raw_cost = _number(result_usage.get("cost_usd"))
            known = raw_cost or 0.0
            # Last-call usage cannot resolve missing retry costs.
            complete = raw_cost is not None and (calls == 1 or (calls is None and isinstance(attempts, list) and len(attempts) == 1))
            upper = known if complete else None
        if not is_model or (not attempted and (not result or status == "BLOCKED_DEPENDENCY")):
            known, complete, upper = 0.0, True, 0.0
        if complete:
            upper = known
        elif upper is not None and upper < known:
            upper = None
        claims = _list(result.get("claim_results"))
        evidence = sorted({item for claim in claims if isinstance(claim, dict)
                           and isinstance(claim.get("evidence_files"), list)
                           for item in claim["evidence_files"] if isinstance(item, str)})
        row = {
            "lane_id": lane_id, "execution": lane.get("execution", "model"),
            "model": lane.get("model"), "reasoning_effort": lane.get("reasoning_effort") or "default",
            "status": status, "what_was_tried": _excerpt(lane.get("role")),
            "result_excerpt": answer, "completed_answer": completed,
            "verification": "Claim statuses are reported evidence; not independently verified by this report.",
            "first_missing_step": _excerpt(result.get("first_missing_step")),
            "evidence_files": evidence, "review_status": "NOT_REVIEWED",
            "attempted": attempted, "known_cost_usd": known, "cost_complete": complete,
            "upper_bound_usd": upper, "wall_seconds": _number(result_usage.get("wall_seconds")),
        }
        rows.append(row)
        if not is_model:
            continue
        key = (str(row["model"] or "unknown"), str(row["reasoning_effort"]))
        group = groups.setdefault(key, {
            "model": key[0], "reasoning_effort": key[1], "scheduled_lanes": 0,
            "attempted_lanes": 0, "completed_answers": 0, "known_cost_usd": 0.0,
            "cost_complete": True, "upper_bound_usd": 0.0,
            "wall_seconds_sum": 0.0, "lanes_with_duration": 0,
            "useful_count": None, "useful_rate": None, "review_status": "NOT_REVIEWED",
        })
        group["scheduled_lanes"] += 1
        group["attempted_lanes"] += int(attempted)
        group["completed_answers"] += int(completed)
        group["known_cost_usd"] += known
        group["cost_complete"] = group["cost_complete"] and complete
        group["upper_bound_usd"] = (group["upper_bound_usd"] + upper
                                    if group["upper_bound_usd"] is not None and upper is not None else None)
        if row["wall_seconds"] is not None:
            group["wall_seconds_sum"] += row["wall_seconds"]
            group["lanes_with_duration"] += 1
    for group in groups.values():
        count = group["attempted_lanes"]
        group["answer_completion_rate"] = group["completed_answers"] / count if count else None
    return {
        "schema_version": "directed-research-report-v1", "task_id": contract.get("task_id"),
        "run_id": root.name, "status": runtime.get("status", "UNKNOWN"),
        "notice": "Operational summary only. No mathematical truth or scientific usefulness has been certified. "
                  "Reservation upper bounds are estimates. Duration sums are lane time, not elapsed run time.",
        "lanes": rows, "model_metrics": list(groups.values()),
        "source_status": "AVAILABLE" if contract else "MISSING_OR_INVALID_CONTRACT",
    }


def render_research_report(report: dict[str, Any]) -> str:
    """Produce a bounded plain Markdown download; caller chooses where to save it."""
    def safe(value: Any) -> str:
        return str(value).replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")

    lines = [f"# Research delivery — {safe(report.get('run_id'))}", "", safe(report.get("notice", "")), ""]
    for row in report.get("lanes", []):
        lines.extend([
            f"## {safe(row['lane_id'])} — {safe(row['status'])}", "",
            f"- Tried: {safe(row['what_was_tried']) or 'Not recorded'}",
            f"- Result: {safe(row['result_excerpt']) or 'No final answer recorded'}",
            f"- Verification: {safe(row['verification'])}",
            f"- Missing step: {safe(row['first_missing_step']) or 'Not recorded'}",
            f"- Evidence references: {safe(', '.join(row['evidence_files'])) or 'None recorded'}",
            f"- Human usefulness review: {safe(row.get('review_status', 'NOT_REVIEWED'))}",
            "- Human usefulness is not a mathematical proof certificate.", "",
        ])
    return "\n".join(lines)
