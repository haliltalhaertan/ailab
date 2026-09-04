from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


@dataclass
class CardState:
    step_key: str
    agent: str
    model: str = ""
    effort: str | None = None
    status: str = "running"
    reasoning: str = ""
    content: str = ""
    total_tokens: int = 0
    reasoning_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    system_prompt: str = ""
    prompt: str = ""
    error: str = ""
    finish_reason: str | None = None
    truncated: bool = False
    requested_max_tokens: int | None = None
    model_max_completion_tokens: int | None = None
    max_tokens_source: str = "provider_default"
    catalog_source: str = "unavailable"
    answer_chars: int = 0
    reasoning_effort_requested: str | None = None
    reasoning_effort_sent: str | None = None
    effort_resolution: str = "provider_default"
    reasoning_max_tokens_sent: int | None = None
    model_default_reasoning_effort: str | None = None
    truncation_retry: str | None = None
    infrastructure_error: str = ""
    expected_min_tokens: int | None = None
    expected_max_tokens: int | None = None
    over_budget: bool = False
    order: int = 0


def _copy_cards(cards: list[CardState]) -> dict[str, CardState]:
    return {card.step_key: replace(card) for card in cards}


def _apply_card_events(cards: list[CardState], events: list[dict[str, Any]]) -> list[CardState]:
    states = _copy_cards(cards)
    latest_by_agent: dict[str, str] = {}
    next_order = 0
    for card in sorted(states.values(), key=lambda item: item.order):
        latest_by_agent[card.agent] = card.step_key
        next_order = max(next_order, card.order)

    def get_state(event: dict[str, Any], *, create: bool = True) -> CardState | None:
        nonlocal next_order
        agent = str(event.get("agent") or "Agent")
        step_key = str(event.get("step_key") or "").strip() or latest_by_agent.get(agent, "")
        if not step_key:
            if not create:
                return None
            next_order += 1
            step_key = f"{agent}:{next_order}"
        state = states.get(step_key)
        if state is None:
            if not create:
                return None
            next_order += 1
            state = CardState(
                step_key=step_key,
                agent=agent,
                model=str(event.get("model") or ""),
                effort=event.get("reasoning_effort"),
                order=next_order,
            )
            states[step_key] = state
        latest_by_agent[agent] = step_key
        return state

    for event in events:
        kind = str(event.get("type") or "")
        if kind not in {"agent_start", "agent_stream", "llm_call", "agent_error", "truncated_retry", "tool_infrastructure_error"}:
            continue
        state = get_state(event, create=kind != "truncated_retry")
        if state is None:
            continue
        if kind == "truncated_retry":
            state.truncation_retry = str(event.get("outcome") or "") or None
            continue
        if kind == "tool_infrastructure_error":
            state.infrastructure_error = str(event.get("error") or "container infrastructure unavailable")
            state.status = "error"
            continue
        if event.get("agent"):
            state.agent = str(event["agent"])
        if event.get("model"):
            state.model = str(event["model"])
        if event.get("reasoning_effort") is not None:
            state.effort = str(event["reasoning_effort"])

        if kind == "agent_start":
            state.status = "running"
            state.system_prompt = str(event.get("system_prompt") or state.system_prompt)
            state.prompt = str(event.get("prompt") or state.prompt)
        elif kind == "agent_stream":
            delta = event.get("delta")
            if not isinstance(delta, str):
                continue
            if event.get("channel") == "reasoning":
                state.reasoning += delta
            elif event.get("channel") == "content":
                state.content += delta
        elif kind == "llm_call":
            state.status = "done"
            if event.get("provider_reasoning"):
                state.reasoning = str(event["provider_reasoning"])
            if event.get("output"):
                state.content = str(event["output"])
            state.total_tokens = int(event.get("total_tokens", 0) or 0)
            state.reasoning_tokens = int(event.get("reasoning_tokens", 0) or 0)
            state.prompt_tokens = int(event.get("prompt_tokens", 0) or 0)
            state.completion_tokens = int(event.get("completion_tokens", 0) or 0)
            state.cached_tokens = int(event.get("cached_tokens", 0) or 0)
            state.cost_usd = float(event["cost_usd"]) if event.get("cost_usd") is not None else None
            state.latency_s = float(event.get("latency_s", 0.0) or 0.0)
            state.finish_reason = str(event["finish_reason"]) if event.get("finish_reason") is not None else None
            state.truncated = bool(event.get("truncated"))
            state.requested_max_tokens = int(event["requested_max_tokens"]) if event.get("requested_max_tokens") is not None else None
            state.model_max_completion_tokens = (
                int(event["model_max_completion_tokens"])
                if event.get("model_max_completion_tokens") is not None
                else None
            )
            state.max_tokens_source = str(event.get("max_tokens_source") or "provider_default")
            state.catalog_source = str(event.get("catalog_source") or "unavailable")
            state.answer_chars = int(event.get("answer_chars", len(str(event.get("output") or ""))) or 0)
            state.reasoning_effort_requested = (
                str(event["reasoning_effort_requested"])
                if event.get("reasoning_effort_requested") is not None
                else None
            )
            state.reasoning_effort_sent = (
                str(event["reasoning_effort_sent"])
                if event.get("reasoning_effort_sent") is not None
                else None
            )
            state.effort_resolution = str(event.get("effort_resolution") or "provider_default")
            state.reasoning_max_tokens_sent = (
                int(event["reasoning_max_tokens_sent"])
                if event.get("reasoning_max_tokens_sent") is not None
                else None
            )
            state.model_default_reasoning_effort = (
                str(event["model_default_reasoning_effort"])
                if event.get("model_default_reasoning_effort") is not None
                else None
            )
            budget = event.get("budget") or {}
            state.expected_min_tokens = int(budget["expected_min"]) if budget.get("expected_min") is not None else None
            state.expected_max_tokens = int(budget["expected_max"]) if budget.get("expected_max") is not None else None
            state.over_budget = bool(budget.get("over_budget"))
        elif kind == "agent_error":
            state.status = "error"
            state.error = str(event.get("error") or "Bilinmeyen hata")

    return sorted(states.values(), key=lambda item: item.order)


def build_cards(events: list[dict[str, Any]]) -> list[CardState]:
    return _apply_card_events([], events)


def merge_cards(cards: list[CardState], events: list[dict[str, Any]]) -> list[CardState]:
    return _apply_card_events(cards, events)


def live_text_preview(text: str, *, limit: int = 4_000) -> tuple[str, bool]:
    value = str(text or "")
    cap = max(1, int(limit))
    if len(value) <= cap:
        return value, False
    return value[-cap:], True


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
    starts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("type") or "")
        step_key = str(event.get("step_key") or "")
        if kind == "stage" and step_key:
            starts[step_key] = event
        elif kind == "stage_end" and step_key:
            start = starts.get(step_key, {})
            budget = event.get("budget") or {}
            rows.append(
                {
                    "step_key": step_key,
                    "label": str(start.get("label") or event.get("label") or step_key),
                    "agent": str(start.get("agent") or event.get("agent") or ""),
                    "started_at": start.get("ts"),
                    "finished_at": event.get("ts"),
                    "duration_s": elapsed_seconds(start.get("ts"), event.get("ts"))
                    if start.get("ts")
                    else float(event.get("latency_s", 0.0) or 0.0),
                    "total_tokens": int(event.get("total_tokens", 0) or 0),
                    "completion_tokens": int(event.get("completion_tokens", 0) or 0),
                    "reasoning_tokens": int(event.get("reasoning_tokens", 0) or 0),
                    "answer_chars": int(event.get("answer_chars", 0) or 0),
                    "cost_usd": event.get("cost_usd"),
                    "finish_reason": event.get("finish_reason"),
                    "truncated": bool(event.get("truncated")),
                    "requested_max_tokens": event.get("requested_max_tokens"),
                    "model_max_completion_tokens": event.get("model_max_completion_tokens"),
                    "max_tokens_source": event.get("max_tokens_source"),
                    "catalog_source": event.get("catalog_source"),
                    "over_budget": bool(budget.get("over_budget")),
                    "expected_max": budget.get("expected_max"),
                    "index": start.get("index", event.get("index")),
                    "total": start.get("total", event.get("total")),
                    "total_is_minimum": bool(start.get("total_is_minimum", event.get("total_is_minimum", False))),
                    "cache": False,
                }
            )
        elif kind == "step_reused" and step_key:
            rows.append(
                {
                    "step_key": step_key,
                    "label": str(event.get("label") or step_key),
                    "agent": str(event.get("agent") or ""),
                    "started_at": event.get("ts"),
                    "finished_at": event.get("ts"),
                    "duration_s": 0.0,
                    "total_tokens": 0,
                    "reasoning_tokens": 0,
                    "cost_usd": None,
                    "finish_reason": None,
                    "truncated": False,
                    "over_budget": False,
                    "expected_max": None,
                    "index": event.get("index"),
                    "total": event.get("total"),
                    "total_is_minimum": bool(event.get("total_is_minimum", False)),
                    "cache": True,
                }
            )
    return rows


def live_stage_snapshot(
    runtime: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    cards: list[CardState] | None = None,
) -> dict[str, Any]:
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
        (event for event in reversed(events) if event.get("type") == "stage_end" and str(event.get("step_key") or "") == step_key),
        None,
    )
    if end is not None:
        tokens = int(end.get("total_tokens", 0) or 0)
        reasoning = int(end.get("reasoning_tokens", 0) or 0)
        elapsed = elapsed_seconds(stage.get("ts"), end.get("ts"))
        estimated = False
    else:
        current_card = next((card for card in cards or [] if card.step_key == step_key), None)
        if current_card is not None:
            tokens = int(round((len(current_card.reasoning) + len(current_card.content)) / 4.0))
            reasoning = int(round(len(current_card.reasoning) / 4.0))
        else:
            visible_chars = 0
            reasoning_chars = 0
            for event in events:
                if event.get("type") != "agent_stream" or str(event.get("step_key") or "") != step_key:
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


def render_stage_timeline(events: list[dict[str, Any]]) -> None:
    import streamlit as st

    rows = stage_timeline(events)
    st.markdown("#### Zaman çizelgesi")
    if not rows:
        st.caption("Tamamlanan aşama henüz yok.")
        return
    for row in rows[-30:]:
        if row.get("cache"):
            index = row.get("index")
            total = row.get("total")
            progress = ""
            if isinstance(index, int) and isinstance(total, int) and total > 0:
                total_label = f"en az {total}" if row.get("total_is_minimum") else str(total)
                progress = f"{index}/{total_label} · "
            st.caption(
                f"{_clock(row.get('started_at'))} · ♻️ {progress}`{row.get('step_key')}` · cache"
            )
            continue
        cost = row.get("cost_usd")
        cost_label = f"${float(cost):.4f}" if cost is not None else "ücret N/A"
        flags = []
        if row.get("truncated"):
            flags.append("⚠️ token sınırında kesildi")
        if row.get("over_budget"):
            expected = row.get("expected_max")
            flags.append(f"ℹ️ beklenen aralığın üstünde{f' (>{expected})' if expected is not None else ''}")
        suffix = " · " + " · ".join(flags) if flags else ""
        st.caption(
            f"{_clock(row.get('started_at'))} → {_clock(row.get('finished_at'))} · "
            f"{row.get('label') or row.get('agent')} · {float(row.get('duration_s', 0.0)):.1f} sn · "
            f"{int(row.get('total_tokens', 0) or 0):,} token · {cost_label}{suffix}"
        )


def render_now_and_timeline(
    runtime: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    status: str,
    cards: list[CardState] | None = None,
    include_timeline: bool = True,
) -> dict[str, Any]:
    import streamlit as st

    snapshot = live_stage_snapshot(runtime, events, cards=cards)
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
        progress_text = f"İlerleme · {index}/en az {total}" if snapshot.get("total_is_minimum") else f"İlerleme · {index}/{total}"
        st.progress(progress, text=progress_text)
    elif status == "RUNNING":
        st.caption("İlerleme · toplam çağrı sayısı bu workflow için önceden kesin değil.")

    if include_timeline:
        render_stage_timeline(events)
    return snapshot
