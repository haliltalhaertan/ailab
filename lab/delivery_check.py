"""Bounded, read-only delivery completeness checks; never scientific validation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAX_BYTES = 16_000_000
MAX_ITEMS = 256
ACTIVE = {"PENDING", "QUEUED", "RUNNING", "FINALIZING"}


def _safe(root: Path, relative: str) -> Path | None:
    # Reject Windows drive/ADS syntax even when tests run on another platform.
    if not relative or ":" in relative or "\\" in relative:
        return None
    part = Path(relative)
    if part.is_absolute() or any(p in {"..", ".env"} for p in part.parts):
        return None
    candidate = root / part
    try:
        if not candidate.resolve().is_relative_to(root):
            return None
        cursor = candidate
        while cursor != root:
            if cursor.is_symlink():
                return None
            cursor = cursor.parent
        return candidate
    except (OSError, ValueError, RuntimeError):
        return None


def _object(root: Path, relative: str, warnings: list[str]) -> dict[str, Any]:
    path = _safe(root, relative)
    try:
        if path is None or not path.is_file() or path.stat().st_size > MAX_BYTES:
            warnings.append(f"Unavailable or unsafe: {relative}")
            return {}
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Oversized document")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("Object required")
        return result
    except (OSError, ValueError, RecursionError):
        warnings.append(f"Invalid document: {relative}")
        return {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def build_delivery_check(run_folder: str | Path) -> dict[str, Any]:
    """Check frozen contract outputs and declared lanes without writing or model calls.

    COMPLETE means nonempty final answers and required package files exist. Claims
    can remain OPEN: their truth and human usefulness are deliberately separate.
    Malformed inputs fail closed as PARTIAL, with bounded diagnostic metadata.
    """
    root = Path(run_folder).resolve()
    warnings: list[str] = []
    contract = _object(root, "TASK_CONTRACT.json", warnings)
    runtime = _object(root, "runtime.json", warnings)
    plan = contract.get("agent_plan")
    raw_lanes = plan.get("lanes") if isinstance(plan, dict) else None
    valid_plan = isinstance(raw_lanes, list) and 0 < len(raw_lanes) <= MAX_ITEMS
    raw_lanes = raw_lanes[:MAX_ITEMS] if isinstance(raw_lanes, list) else []
    states = runtime.get("lanes")
    states = states if isinstance(states, dict) else {}
    rows = []
    seen: set[str] = set()
    for declaration in raw_lanes:
        lane = declaration.get("lane_id") if isinstance(declaration, dict) else None
        if not isinstance(lane, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", lane) or lane in seen:
            valid_plan = False
            warnings.append("Invalid or duplicate lane declaration")
            continue
        seen.add(lane)
        relative = f"lanes/{lane}/RESULT.json"
        candidate = _safe(root, relative)
        result = _object(root, relative, warnings) if candidate is not None and candidate.exists() else {}
        state = _text(result.get("status")) or _text(states.get(lane)) or "PENDING"
        # RESPONSE alone is not a committed final result; reasoning is never an answer.
        answer = _text(result.get("public_findings"))
        if not answer and result.get("status") == "COMPLETED":
            response = _object(root, f"lanes/{lane}/RESPONSE.json", warnings)
            answer = _text(response.get("content"))
        delivery = "COMPLETE" if state == "COMPLETED" and answer else "PENDING" if state in ACTIVE else "PARTIAL"
        claims = result.get("claim_results")
        claims = claims[:MAX_ITEMS] if isinstance(claims, list) else []
        statuses = sorted({_text(c.get("status")) for c in claims if isinstance(c, dict)} - {""})
        rows.append({"lane_id": lane, "status": state, "delivery_status": delivery,
                     "has_final_answer": bool(answer), "declared_claim_statuses": statuses,
                     "first_missing_step": _text(result.get("first_missing_step"))[:2000]})
    if not valid_plan:
        warnings.append("Missing, invalid or oversized lane plan")
    required = contract.get("required_outputs")
    valid_outputs = isinstance(required, list) and 0 < len(required) <= MAX_ITEMS
    required = required[:MAX_ITEMS] if isinstance(required, list) else []
    outputs = []
    for name in required:
        path = _safe(root, f"package/{name}") if isinstance(name, str) and _safe(root, name) else None
        status = "UNSAFE"
        if path is not None:
            try:
                status = "PRESENT" if path.is_file() and path.stat().st_size else "EMPTY" if path.is_file() else "MISSING"
            except OSError:
                status = "MISSING"
        outputs.append({"path": name[:1000] if isinstance(name, str) else "<invalid>", "status": status})
    if not valid_outputs:
        warnings.append("Missing, invalid or oversized required outputs")
    delivered = bool(rows) and all(row["delivery_status"] == "COMPLETE" for row in rows)
    files_ready = bool(outputs) and all(row["status"] == "PRESENT" for row in outputs)
    run_status = _text(runtime.get("status")) or "UNKNOWN"
    if valid_plan and valid_outputs and delivered and files_ready and not warnings:
        overall = "COMPLETE"
    elif valid_plan and valid_outputs and run_status in ACTIVE and any(row["delivery_status"] == "PENDING" for row in rows):
        overall = "PENDING"
    else:
        overall = "PARTIAL"
    return {"schema_version": "delivery-check-v1", "run_id": root.name,
            "task_id": contract.get("task_id"), "run_status": run_status,
            "delivery_status": overall, "lanes": rows, "required_outputs": outputs,
            "human_review": "NOT_ASSESSED", "scientific_validation": "NOT_PERFORMED",
            "notice": "Completion checks output presence only; declared claim statuses are not verified. "
                      "This check neither proves mathematical truth nor rates scientific usefulness.",
            "warnings": warnings}
