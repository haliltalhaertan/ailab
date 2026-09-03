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
                    "total_is_minimum": bool(
                        start.get("total_is_minimum", event.get("total_is_minimum", False))
                    ),
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
            "total_is_minimum": False,
            "elapsed_s": 0.0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "token_estimated": True,
            "model": "",
            "reasoning_effort": None,
            "step_key": "",
            "finished": False,
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
        "total_is_minimum": bool(stage.get("total_is_minimum")),
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


def _clock(value: Any) -> str:
    parsed = parse_ts(value)
    return parsed.astimezone().strftime("%H:%M:%S") if parsed else "--:--:--"


def render_now_and_timeline(
    runtime: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    """Shared Streamlit component used by the home page and Research Control."""

    import streamlit as st

    snapshot = live_stage_snapshot(runtime, events)
    st.markdown("#### Şimdi")
    label = snapshot.get("label") or "Worker hazırlanıyor"
    token_prefix = "~" if snapshot.get("token_estimated") else ""
    reasoning = int(snapshot.get("reasoning_tokens", 0) or 0)
    line = (
        f"**{label}** · {format_duration(float(snapshot.get('elapsed_s', 0.0) or 0.0))} · "
        f"{token_prefix}{int(snapshot.get('total_tokens', 0) or 0):,} token "
        f"(reasoning {token_prefix}{reasoning:,})"
    )
    st.markdown(line)
    details = []
    if snapshot.get("agent"):
        details.append(str(snapshot["agent"]))
    if snapshot.get("model"):
        details.append(str(snapshot["model"]))
    details.append(f"effort: {snapshot.get('reasoning_effort') or 'provider-default'}")
    st.caption(" · ".join(details))

    age = heartbeat_age_seconds(runtime)
    if status == "STALE_RUNNING":
        st.error("Worker stale görünüyor. Research Control üzerinden stale run temizliği yap.")
        st.page_link("pages/3_Research_Control.py", label="Research Control")
    elif status == "RUNNING" and age is not None and age > 120:
        st.warning(f"Worker {int(age)} saniyedir heartbeat vermedi; worker sessiz olabilir.")

    index = snapshot.get("index")
    total = snapshot.get("total")
    if isinstance(index, int) and isinstance(total, int) and total > 0:
        progress = min(1.0, max(0.0, index / total))
        if snapshot.get("total_is_minimum"):
            progress_text = f"İlerleme · {index}/en az {total}"
        else:
            progress_text = f"İlerleme · {index}/{total}"
        st.progress(progress, text=progress_text)
    elif status == "RUNNING":
        st.caption("İlerleme · toplam çağrı sayısı bu workflow için önceden kesin değil.")

    rows = stage_timeline(events)
    st.markdown("#### Zaman çizelgesi")
    if not rows:
        st.caption("Tamamlanan aşama henüz yok.")
    else:
        for row in rows[-30:]:
            cost = row.get("cost_usd")
            cost_text = f"${float(cost):.4f}" if cost is not None else "ücret N/A"
            st.caption(
                f"{_clock(row.get('started_at'))} → {_clock(row.get('finished_at'))} · "
                f"{row.get('label') or row.get('agent')} · {float(row.get('duration_s', 0.0)):.1f} sn · "
                f"{int(row.get('total_tokens', 0) or 0):,} token · {cost_text}"
            )
    return snapshot
