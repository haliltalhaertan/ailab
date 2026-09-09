"""Offline, bounded research-assistant evaluation; never invokes a model or executes answers."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, TypeGuard

SUITE_ID = "research-assistant-small-v1"
DISCLAIMER = (
    "Machine checks validate only the stated finite tasks. Prose is not verified proof. "
    "This benchmark does not establish research novelty or prove the Collatz conjecture."
)


def tasks() -> list[dict[str, Any]]:
    """Public tasks, including schemas and review criteria, without answer keys."""
    return [
        {"task_id": "sum-squares", "mode": "exact", "prompt":
         "Compute the exact integer sum of k^2 for k=1,...,100.",
         "answer_schema": {"value": "integer"}},
        {"task_id": "modular-inverse", "mode": "exact", "prompt":
         "Find the unique integer x in [0,42] with 17*x congruent to 1 modulo 43.",
         "answer_schema": {"value": "integer"}},
        {"task_id": "polynomial-counterexample", "mode": "exact", "prompt":
         "Refute: n^2+n+41 is prime for every integer n>=0. Supply n in [0,100] "
         "and a proper positive divisor d of n^2+n+41.",
         "answer_schema": {"n": "integer", "d": "integer"}},
        {"task_id": "collatz-prefix", "mode": "exact", "prompt":
         "Use T(n)=n/2 for even n and 3*n+1 for odd n. Starting at 7, return "
         "the next five values, excluding 7. Make no asymptotic or universal claim.",
         "answer_schema": {"values": "five integers"}},
        {"task_id": "division-audit", "mode": "human_review", "prompt":
         "Audit this proposed proof: a=b=1; a^2=ab; a^2-b^2=ab-b^2; "
         "(a-b)(a+b)=b(a-b); cancel a-b to get a+b=b, hence 2=1. "
         "Name the invalid step and its missing condition.",
         "answer_schema": {"analysis": "text"},
         "rubric": ["Identifies division/cancellation by a-b=0.",
                    "States cancellation requires a-b != 0.",
                    "Does not endorse the conclusion 2=1."]},
        {"task_id": "contraction-audit", "mode": "human_review", "prompt":
         "Audit: f(z)=|cos(pi*z)|<1 for every noninteger real z; therefore there "
         "exists c<1 with f(z)<=c for all noninteger real z. Give a concrete "
         "sequence and explain whether the inference is valid.",
         "answer_schema": {"analysis": "text"},
         "rubric": ["Rejects the uniform bound over all nonintegers.",
                    "Gives noninteger z_n approaching an integer, e.g. 1+1/n for n>=2.",
                    "Explains f(z_n) tends to 1 by continuity."]},
    ]


def export_suite() -> dict[str, Any]:
    return {"suite_id": SUITE_ID, "disclaimer": DISCLAIMER, "tasks": tasks()}


def _integer(value: Any) -> TypeGuard[int]:
    return type(value) is int


def _check(task_id: str, answer: dict[str, Any]) -> bool:
    value = answer.get("value")
    if task_id == "sum-squares":
        return _integer(value) and value == sum(k * k for k in range(1, 101))
    if task_id == "modular-inverse":
        return _integer(value) and 0 <= value < 43 and (17 * value) % 43 == 1
    if task_id == "polynomial-counterexample":
        n, d = answer.get("n"), answer.get("d")
        if not _integer(n) or not _integer(d) or not 0 <= n <= 100:
            return False
        polynomial = n * n + n + 41
        return 1 < d < polynomial and polynomial % d == 0
    if task_id == "collatz-prefix":
        values = answer.get("values")
        n, expected = 7, []
        for _ in range(5):
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            expected.append(n)
        return (isinstance(values, list) and len(values) == 5
                and all(_integer(item) for item in values) and values == expected)
    raise ValueError(f"No exact checker for {task_id}")


def _nonnegative(value: Any, name: str, integer: bool = False) -> Any:
    if value is None:
        return None
    valid_type = _integer(value) if integer else type(value) in (int, float)
    if not valid_type or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number or null")
    return value


def evaluate(submission: dict[str, Any]) -> dict[str, Any]:
    """Score external responses. Usage is self-reported, never inferred from missing values."""
    if not isinstance(submission, dict):
        raise ValueError("Submission must be an object")
    if submission.get("suite_id") != SUITE_ID:
        raise ValueError("suite_id mismatch")
    responses = submission.get("responses")
    if not isinstance(responses, list):
        raise ValueError("responses must be a list")
    model, effort = submission.get("model"), submission.get("reasoning_effort")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a nonempty string")
    if effort is not None and not isinstance(effort, str):
        raise ValueError("reasoning_effort must be text or null")
    usage = submission.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("usage must be an object")
    normalized_usage = {key: _nonnegative(usage.get(key), key, integer=True)
                        for key in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens")}
    cost = _nonnegative(usage.get("cost_usd"), "cost_usd")
    complete = usage.get("cost_complete", False)
    if type(complete) is not bool or (complete and cost is None):
        raise ValueError("cost_complete requires a known cost_usd and a boolean flag")
    normalized_usage.update(cost_usd=cost, cost_complete=complete)
    elapsed = _nonnegative(submission.get("elapsed_seconds"), "elapsed_seconds")
    definitions = {item["task_id"]: item for item in tasks()}
    indexed: dict[str, dict[str, Any]] = {}
    for response in responses:
        if not isinstance(response, dict):
            raise ValueError("Every response must be an object")
        task_id = response.get("task_id")
        if not isinstance(task_id, str) or task_id not in definitions or task_id in indexed:
            raise ValueError("Unknown or duplicate task_id")
        answer = response.get("answer")
        if not isinstance(answer, dict):
            raise ValueError("answer must be an object")
        indexed[task_id] = answer
    results = []
    for task_id, definition in definitions.items():
        answer = indexed.get(task_id)
        status = "MISSING"
        if answer is not None:
            if definition["mode"] == "exact":
                status = "EXACT_PASS" if _check(task_id, answer) else "FAIL"
            else:
                analysis = answer.get("analysis")
                status = "REVIEW_REQUIRED" if isinstance(analysis, str) and analysis.strip() else "FAIL"
        results.append({"task_id": task_id, "status": status, "mode": definition["mode"]})
    passed = sum(row["status"] == "EXACT_PASS" for row in results)
    return {
        "suite_id": SUITE_ID, "disclaimer": DISCLAIMER, "model": model,
        "reasoning_effort": effort, "results": results,
        "exact_passes": passed, "exact_tasks": 4, "exact_pass_rate": passed / 4,
        "submitted_tasks": len(indexed), "total_tasks": len(definitions),
        "human_review_pending": sum(row["status"] == "REVIEW_REQUIRED" for row in results),
        "usage_source": "UNVERIFIED_SUBMISSION_METADATA", "usage": normalized_usage,
        "elapsed_seconds": elapsed,
        "cost_per_exact_pass_usd": cost / passed if complete and passed and cost is not None else None,
        "scientific_discovery_established": False, "formal_kernel_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    score = sub.add_parser("score")
    score.add_argument("submission", type=Path)
    score.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate(json.loads(args.submission.read_text(encoding="utf-8"))) if args.command == "score" else export_suite()
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        output = getattr(args, "output", None)
        if output:
            output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
