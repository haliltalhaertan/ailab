"""Bounded offline candidate archive. Answers are data, never executable programs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from lab.directed_eval import DISCLAIMER, SUITE_ID, evaluate, tasks

MAX_CANDIDATES = 256
MAX_BYTES = 2_000_000
ARCHIVE_SCHEMA = "research-candidates-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def evaluate_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a finite candidate DAG using public directed_eval checks; no calls or execution."""
    try:
        encoded = _canonical(payload).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Payload must contain finite JSON data") from exc
    if len(encoded) > MAX_BYTES:
        raise ValueError("Candidate payload exceeds 2000000 bytes")
    if not isinstance(payload, dict) or payload.get("suite_id") != SUITE_ID:
        raise ValueError("suite_id mismatch")
    if set(payload) - {"suite_id", "candidates"}:
        raise ValueError("Unknown payload fields")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("candidates must contain 1 to 256 entries")
    indexed: dict[str, dict[str, Any]] = {}
    allowed = {"candidate_id", "parent_id", "task_id", "answer", "model", "reasoning_effort",
               "usage", "elapsed_seconds"}
    definitions = {task["task_id"]: task for task in tasks()}
    for item in candidates:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("Invalid candidate fields")
        candidate_id = item.get("candidate_id")
        if (not isinstance(candidate_id, str) or not candidate_id.strip()
                or len(candidate_id) > 128 or candidate_id in indexed):
            raise ValueError("candidate_id must be unique nonempty text, at most 128 characters")
        if not isinstance(item.get("task_id"), str) or item["task_id"] not in definitions:
            raise ValueError("Unknown task_id")
        if not isinstance(item.get("answer"), dict):
            raise ValueError("answer must be an object")
        if "usage" in item and not isinstance(item["usage"], dict):
            raise ValueError("usage must be an object")
        indexed[candidate_id] = item
    for candidate_id, item in indexed.items():
        parent = item.get("parent_id")
        if parent is not None:
            if not isinstance(parent, str) or parent not in indexed:
                raise ValueError("Unknown parent_id")
            if indexed[parent]["task_id"] != item["task_id"]:
                raise ValueError("Parent and child must address the same task")
        seen = {candidate_id}
        while parent is not None:
            if parent in seen:
                raise ValueError("Candidate lineage contains a cycle")
            seen.add(parent)
            ancestor = indexed.get(parent)
            if ancestor is None:
                raise ValueError("Unknown parent_id")
            parent = ancestor.get("parent_id")
            if parent is not None and not isinstance(parent, str):
                raise ValueError("parent_id must be text or null")
    rows: list[dict[str, Any]] = []
    cache: dict[str, tuple[str, dict[str, Any]]] = {}
    for candidate_id in sorted(indexed):
        item = indexed[candidate_id]
        task_id = item["task_id"]
        fingerprint = hashlib.sha256(_canonical({"task_id": task_id, "answer": item["answer"]})
                                     .encode("utf-8")).hexdigest()
        submission = {"suite_id": SUITE_ID, "model": item.get("model"),
                      "reasoning_effort": item.get("reasoning_effort"), "usage": item.get("usage", {}),
                      "elapsed_seconds": item.get("elapsed_seconds"), "responses": []}
        # Validate each provenance record even when its answer duplicates an earlier one.
        provenance = evaluate(submission)
        duplicate_of = None
        if fingerprint in cache:
            duplicate_of, result = cache[fingerprint]
        else:
            submission["responses"] = [{"task_id": task_id, "answer": item["answer"]}]
            result = next(row for row in evaluate(submission)["results"] if row["task_id"] == task_id)
            cache[fingerprint] = (candidate_id, result)
        status = result["status"]
        rows.append({"candidate_id": candidate_id, "parent_id": item.get("parent_id"),
                     "task_id": task_id, "answer": item["answer"], "fingerprint": fingerprint,
                     "duplicate_of": duplicate_of, "status": status,
                     "exact_score": (1 if status == "EXACT_PASS" else 0) if result["mode"] == "exact" else None,
                     "provenance": {key: provenance[key] for key in
                                    ("model", "reasoning_effort", "usage_source", "usage", "elapsed_seconds")}})
    selected = {task_id: [row["candidate_id"] for row in rows if row["task_id"] == task_id
                         and row["status"] == "EXACT_PASS" and row["duplicate_of"] is None]
                for task_id in sorted(definitions) if definitions[task_id]["mode"] == "exact"}
    return {"schema": ARCHIVE_SCHEMA, "suite_id": SUITE_ID, "disclaimer": DISCLAIMER,
            "candidates": rows, "rankings_by_task": selected,
            "ranking_policy": "Exact passes only; all ties ordered by candidate_id, not mathematical quality. "
                              "Exact duplicates share one evaluation and are not independent votes.",
            "candidate_count": len(rows), "unique_evaluations": len(cache),
            "duplicate_count": len(rows) - len(cache),
            "review_required_count": sum(row["status"] == "REVIEW_REQUIRED" for row in rows),
            "scientific_discovery_established": False, "formal_kernel_verified": False,
            "model_calls": 0, "new_tasks_started": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.submission.stat().st_size > MAX_BYTES:
            raise ValueError("Candidate payload exceeds 2000000 bytes")
        result = evaluate_candidates(json.loads(args.submission.read_text(encoding="utf-8")))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    except (ValueError, TypeError, OSError, RecursionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
