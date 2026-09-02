from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def elapsed_seconds(start: Any, end: Any | None = None) -> float:
    started = parse_ts(start)
    finished = parse_ts(end) if end is not None else datetime.now(timezone.utc)
    if started is None or finished is None:
        return 0.0
    return max(0.0, (finished - started).total_seconds())


def stage_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair stage/stage_end records into display-ready chronological rows."""

    starts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("type") or "")
        step_key = str(event.get("step_key") or "")
        if kind == "stage" and step_key:
            starts[step_key] = event
        elif kind == "stage_end" and step_key:
            start = starts.get(step_key, {})
            rows.append(
                {
                    "step_key": step_key,
                    "label": str(start.get("label") or event.get("label") or step_key),
                    "agent": str(start.get("agent") or event.get("agent") or ""),
                    "started_at": start.get("ts"),
                    "finished_at": event.get("ts"),
                    "duration_s": elapsed_seconds(
                        start.get("ts"),
                        event.get("ts"),
                    ) if start.get("ts") else float(event.get("latency_s", 0.0) or 0.0),
                    "total_tokens": int(event.get("total_tokens", 0) or 0),
                    "reasoning_tokens": int(event.get("reasoning_tokens", 0) or 0),
                    "cost_usd": event.get("cost_usd"),
                    "index": start.get("index", event.get("index")),
                    "total": start.get("total", event.get("total")),
                }
            )
    return rows


def live_stage_snapshot(
    runtime: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the current/last stage, progress and live token estimate.

    During a streaming call token count is deliberately marked as an estimate
    based on visible characters/4. Once stage_end exists, provider totals win.
    """

    now = now or datetime.now(timezone.utc)
    stages = [event for event in events if event.get("type") == "stage"]
    if not stages:
        return {
            "label": str(runtime.get("current_step") or ""),
            "agent": str(runtime.get("current_agent") or ""),
            "index": None,
            "total": None,
            "elapsed_s": 0.0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "token_estimated": True,
            "model": "",
            "reasoning_effort": None,
            "step_key": "",
        }

    stage = stages[-1]
    step_key = str(stage.get("step_key") or "")
    end = next(
        (
            event
            for event in reversed(events)
            if event.get("type") == "stage_end"
            and str(event.get("step_key") or "") == step_key
        ),
        None,
    )
    if end is not None:
        tokens = int(end.get("total_tokens", 0) or 0)
        reasoning = int(end.get("reasoning_tokens", 0) or 0)
        elapsed = elapsed_seconds(stage.get("ts"), end.get("ts"))
        estimated = False
    else:
        visible_chars = 0
        reasoning_chars = 0
        for event in events:
            if event.get("type") != "agent_stream":
                continue
            if str(event.get("step_key") or "") != step_key:
                continue
            delta = event.get("delta")
            if not isinstance(delta, str):
                continue
            visible_chars += len(delta)
            if event.get("channel") == "reasoning":
                reasoning_chars += len(delta)
        tokens = int(round(visible_chars / 4.0))
        reasoning = int(round(reasoning_chars / 4.0))
        started = parse_ts(stage.get("ts"))
        elapsed = max(0.0, (now - started).total_seconds()) if started else 0.0
        estimated = True

    return {
        "label": str(stage.get("label") or runtime.get("current_step") or step_key),
        "agent": str(stage.get("agent") or runtime.get("current_agent") or ""),
        "index": stage.get("index"),
        "total": stage.get("total"),
        "elapsed_s": elapsed,
        "total_tokens": tokens,
        "reasoning_tokens": reasoning,
        "token_estimated": estimated,
        "model": str(stage.get("model") or ""),
        "reasoning_effort": stage.get("reasoning_effort"),
        "step_key": step_key,
        "finished": end is not None,
    }


def heartbeat_age_seconds(runtime: dict[str, Any], *, now: datetime | None = None) -> float | None:
    heartbeat = parse_ts(runtime.get("heartbeat_at") or runtime.get("updated_at"))
    if heartbeat is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - heartbeat).total_seconds())


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
