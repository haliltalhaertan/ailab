from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def filter_models(model_ids: list[str], model_labels: dict[str, str], query: str) -> list[str]:
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(model_ids)
    return [m for m in model_ids if needle in m.casefold() or needle in model_labels.get(m, "").casefold()]


def cost_text(summary: dict[str, Any]) -> str:
    return f"{'' if summary.get('cost_complete', False) else '≥'}${float(summary.get('total_cost_usd', 0.0)):.6f}"


def usage_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "Ajan": name,
            "Model": ", ".join(v.get("models", [])),
            "Çağrı": v.get("calls", 0),
            "Input": v.get("prompt_tokens", 0),
            "Output": v.get("completion_tokens", 0),
            "Reasoning": v.get("reasoning_tokens", 0),
            "Cached": v.get("cached_tokens", 0),
            "Toplam": v.get("total_tokens", 0),
            "Ücret ($)": round(float(v.get("cost_usd", 0)), 8),
            "Süre (sn)": round(float(v.get("latency_s", 0)), 2),
        }
        for name, v in summary.get("agents", {}).items()
    ]


def event_summary(event: dict[str, Any]) -> str:
    kind = event.get("type", "event")
    if kind == "agent_start":
        return f"{event.get('agent')} başladı ({event.get('model')})"
    if kind == "llm_call":
        return f"{event.get('agent')} tamamlandı: {event.get('total_tokens', 0)} token"
    if kind == "tool_start":
        return f"Tool başladı: {(event.get('request') or {}).get('tool', '?')}"
    if kind == "tool_result":
        return f"Tool sonucu: {event.get('tool')} ok={event.get('ok')}"
    if kind == "state_change":
        return f"State: {event.get('item_id')} {event.get('old_status', '')}→{event.get('new_status', event.get('status', ''))}"
    if kind == "status_downgraded_by_guard":
        return f"Guard: {event.get('requested')}→{event.get('granted')}"
    if kind == "checkpoint":
        return "Checkpoint kaydedildi"
    if kind == "literature_search":
        return f"Literatür: {len(event.get('results', []))} kayıt"
    return str(kind)


def tool_status(event: dict[str, Any]) -> tuple[str, str]:
    error = str(event.get("error") or "").strip()
    metadata = event.get("metadata") or {}
    metadata_status = str(metadata.get("status") or "").upper()
    if error:
        return "HATA", "error"
    if metadata_status == "COUNTEREXAMPLE":
        return "COUNTEREXAMPLE", "error"
    if bool(event.get("ok")):
        return "PASS", "success"
    return "FAIL", "error"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
    return result


def load_run_events(run_dir: Path, *, include_stream: bool = True) -> list[dict[str, Any]]:
    events = read_jsonl(run_dir / "trace.jsonl")
    if include_stream:
        events.extend(read_jsonl(run_dir / "stream.jsonl"))
    events.sort(key=lambda ev: str(ev.get("ts") or ""))
    return events


def read_run_index(runs_dir: Path) -> list[dict[str, Any]]:
    path = runs_dir / "index.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        merged = dict(latest.get(run_id) or {})
        merged.update(row)
        latest[run_id] = merged
    return sorted(latest.values(), key=lambda row: str(row.get("ts") or ""), reverse=True)


def runs_for_project(runs_dir: Path, project_id: str, project_uuid: str | None = None) -> list[Path]:
    rows = read_run_index(runs_dir)
    selected = []
    for row in rows:
        if str(row.get("project_id") or "") != project_id:
            continue
        if project_uuid and row.get("project_uuid") and str(row.get("project_uuid")) != project_uuid:
            continue
        path = Path(str(row.get("run_dir") or ""))
        if path.exists():
            selected.append(path)
    return selected
