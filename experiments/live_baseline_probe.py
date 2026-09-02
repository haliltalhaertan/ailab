from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from pathlib import Path

from lab.agent import Agent
from lab.integrity_theorem_lab import TheoremResearchLab
from lab.research_state import ResearchState
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        return []


def make_agent(name: str, model: str, role: str) -> Agent:
    return Agent(
        name=name,
        system_prompt=(
            f"You are the {role} in a research-system plumbing baseline. "
            "Follow the requested JSON schema exactly. Keep the mathematical content simple, "
            "do not exaggerate evidence, and do not claim formal proof without machine evidence."
        ),
        model=model,
        temperature=0.1,
        max_tokens=1400,
        reasoning_effort="low",
    )


def summarize(trace: Trace, state: ResearchState, outcome_text: str) -> dict:
    events: list[dict] = []
    with trace.path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    counts = Counter(str(event.get("type") or "") for event in events)
    repair_events = [event for event in events if "repair" in str(event.get("type") or "").lower()]
    downgrades = [event for event in events if event.get("type") == "status_downgraded_by_guard"]
    llm_calls = [event for event in events if event.get("type") == "llm_call"]
    per_agent = Counter(str(event.get("agent") or "unknown") for event in llm_calls)

    candidates = state.list_items(kind="conjecture")
    return {
        "outcome_preview": outcome_text[:1200],
        "trace_event_counts": dict(sorted(counts.items())),
        "json_repair_event_count": len(repair_events),
        "json_repair_events": repair_events,
        "guard_downgrade_count": len(downgrades),
        "guard_downgrades": downgrades,
        "llm_call_count": len(llm_calls),
        "llm_calls_per_agent": dict(sorted(per_agent.items())),
        "candidate_statuses": [
            {"id": item.id, "title": item.title, "status": item.status, "claim": item.claim}
            for item in candidates
        ],
    }


def main() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for the live baseline probe")

    model = os.environ.get("LAB_BASELINE_MODEL") or "openai/gpt-4o-mini"
    root = Path(os.environ.get("LAB_BASELINE_OUT") or "baseline_artifacts")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    state = ResearchState(root / "research_state")
    trace = Trace("two_iteration_live_baseline", out_dir=root / "runs")
    report = ""
    error: BaseException | None = None
    try:
        lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
        report = lab.run(
            (
                "PLUMBING BASELINE ONLY. Investigate the elementary identity "
                "sum_{k=1}^n k = n(n+1)/2 for integers n >= 0. "
                "The scientific content is intentionally trivial; the purpose is to exercise the full worker/run path. "
                "Do not request Lean, Z3, tropical_grid, scripts, or code_experiment in this baseline; use tool_request {\"tool\":\"none\"}. "
                "Do not label anything PROVEN merely from LLM agreement."
            ),
            manager=make_agent("ResearchManager", model, "research manager"),
            proposer=make_agent("Theorist", model, "theorist"),
            critic=make_agent("AdversarialCritic", model, "adversarial critic"),
            verifier=make_agent("VerificationEngineer", model, "verification engineer"),
            auditor=make_agent("IndependentAuditor", model, "independent auditor"),
            iterations=2,
            checkpoint_every=0,
        )
    except BaseException as exc:  # preserve artifacts even on fail-closed behavior
        error = exc
        report = f"probe raised: {exc!r}"
    finally:
        summary_path = trace.close()

    probe_summary = summarize(trace, state, report)
    trace_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    probe_summary["trace_summary"] = trace_summary
    probe_summary["model"] = model
    probe_summary["error"] = repr(error) if error else None
    output = root / "probe_summary.json"
    output.write_text(json.dumps(probe_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(probe_summary, indent=2, ensure_ascii=False))
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
