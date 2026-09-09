"""Human usefulness feedback kept outside frozen scientific run artifacts.

Reviewer names are self-reported labels, not authenticated identities. This is
an append-only application journal, not a cryptographically trusted audit log.
Concurrent writers use an exclusive file lock; interrupted writers require an
operator to remove the leftover .lock file after confirming the writer stopped.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator
from uuid import uuid4

from lab.directed_reports import build_research_report

_LOCK = threading.RLock()
_SCHEMA = "human-research-reviews-v1"


@contextmanager
def _writer_lock(store: Path) -> Iterator[None]:
    store.parent.mkdir(parents=True, exist_ok=True)
    lock = store.with_name(store.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Review store has an active or interrupted writer; retry after checking its .lock file") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def _object(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError(f"Missing, linked or oversized review input: {path.name}")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Review input must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _paths(store_path: str | Path, run_folder: str | Path) -> tuple[Path, Path]:
    root = Path(run_folder).resolve()
    original = Path(store_path)
    store = original.resolve()
    if original.is_symlink() or store.is_relative_to(root):
        raise ValueError("Review store must be outside the frozen run and not a symlink")
    if store.suffix != ".json":
        raise ValueError("Review store must be a .json file")
    return store, root


def _bindings(root: Path, lane_id: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", lane_id):
        raise ValueError("Invalid lane identifier")
    contract, contract_hash = _object(root / "TASK_CONTRACT.json")
    lanes = contract.get("agent_plan", {}).get("lanes", [])
    if not any(isinstance(item, dict) and item.get("lane_id") == lane_id for item in lanes):
        raise ValueError("Lane is not declared in the contract")
    result_path = root / "lanes" / lane_id / "RESULT.json"
    if not result_path.resolve().is_relative_to(root):
        raise ValueError("Result escapes run folder")
    _, result_hash = _object(result_path)
    return {"contract_sha256": contract_hash, "result_sha256": result_hash}


def _events(store: Path) -> list[dict[str, Any]]:
    if not store.exists():
        return []
    obj, _ = _object(store)
    events = obj.get("events")
    if obj.get("schema_version") != _SCHEMA or not isinstance(events, list):
        raise ValueError("Invalid review journal; refusing to overwrite")
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("Invalid review event")
    return events


def record_review(
    store_path: str | Path, run_folder: str | Path, lane_id: str,
    decision: str, note: str, reviewer: str, review_minutes: float,
    *, expected_bindings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Append feedback bound to exact contract/result bytes; never edits the run."""
    if decision not in {"useful", "not_useful", "needs_work"}:
        raise ValueError("Unknown usefulness decision")
    if not isinstance(note, str) or len(note) > 20_000:
        raise ValueError("Review note must contain at most 20000 characters")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
        raise ValueError("A reviewer label of at most 200 characters is required")
    if isinstance(review_minutes, bool) or not isinstance(review_minutes, (int, float)) or not math.isfinite(review_minutes) or review_minutes < 0:
        raise ValueError("Review minutes must be finite and nonnegative")
    store, root = _paths(store_path, run_folder)
    with _LOCK, _writer_lock(store):
        bindings = _bindings(root, lane_id)
        if expected_bindings is not None and bindings != expected_bindings:
            raise ValueError('Result changed since preview; load and inspect the result again')
        events = _events(store)
        event = {
            "event_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
            "run_folder": str(root), "lane_id": lane_id, **bindings,
            "decision": decision, "note": note, "reviewer": reviewer.strip(),
            "reviewer_authenticated": False, "review_minutes": float(review_minutes),
        }
        events.append(event)
        store.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=store.name + ".", suffix=".tmp", dir=store.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": _SCHEMA, "events": events}, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            # A live result may have changed while feedback was being prepared.
            if _bindings(root, lane_id) != bindings:
                raise ValueError("Result changed while recording review; inspect it again")
            os.replace(temporary, store)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return event


def review_snapshot(run_folder: str | Path, lane_id: str) -> dict[str, Any]:
    """Return the exact result and bindings that a reviewer is being shown."""
    root = Path(run_folder).resolve()
    bindings = _bindings(root, lane_id)
    result, result_hash = _object(root / 'lanes' / lane_id / 'RESULT.json')
    if result_hash != bindings['result_sha256'] or _bindings(root, lane_id) != bindings:
        raise ValueError('Result changed while loading the review preview; retry')
    return {'bindings': bindings, 'result': result}


def load_reviews(store_path: str | Path, run_folder: str | Path) -> dict[str, dict[str, Any]]:
    """Return latest event per lane. Changed/missing source bytes mark it stale."""
    store, root = _paths(store_path, run_folder)
    with _LOCK:
        events = _events(store)
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("run_folder") != str(root):
            continue
        lane_id = event.get("lane_id")
        if not isinstance(lane_id, str):
            raise ValueError("Invalid review lane")
        latest[lane_id] = dict(event)
    for lane_id, event in latest.items():
        try:
            bindings = _bindings(root, lane_id)
            current = all(event.get(key) == value for key, value in bindings.items())
        except (OSError, ValueError, TypeError, AttributeError):
            current = False
        event.update(current=current, stale=not current)
    return latest


def build_reviewed_report(run_folder: str | Path, store_path: str | Path) -> dict[str, Any]:
    """Enrich operational report with current human feedback, never claim status.

    useful_rate denominator is all currently reviewed lanes, including needs_work.
    Cost per useful result covers the whole model/effort group, including failed
    and unreviewed work, and is available only when every lane cost is complete.
    """
    report = build_research_report(run_folder)
    reviews = load_reviews(store_path, run_folder)
    for row in report["lanes"]:
        review = reviews.get(row["lane_id"])
        row["human_review"] = review
        row["review_status"] = ("STALE" if review["stale"] else review["decision"]) if review else "NOT_REVIEWED"
    for group in report["model_metrics"]:
        rows = [row for row in report["lanes"] if row["execution"] == "model"
                and str(row["model"] or "unknown") == group["model"]
                and row["reasoning_effort"] == group["reasoning_effort"]]
        current = [row["human_review"] for row in rows if row["human_review"] and row["human_review"]["current"]]
        useful = sum(review["decision"] == "useful" for review in current)
        group.update(
            reviewed_count=len(current), useful_count=useful,
            useful_rate=useful / len(current) if current else None,
            review_minutes=sum(review["review_minutes"] for review in current),
            review_status="HUMAN_REVIEWED" if current else "NOT_REVIEWED",
            cost_per_useful_usd=group["known_cost_usd"] / useful if useful and group["cost_complete"] else None,
        )
    report["notice"] += " Human usefulness decisions are self-reported feedback, not mathematical proof or authenticated approval. Stale reviews are excluded from metrics."
    return report
