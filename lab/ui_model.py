from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


DEFAULT_AGENT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "experiments" / "baseline_production_agents.json"


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


def load_default_agent_profile(path: Path | None = None) -> dict[str, Any]:
    """Load checked-in UI/model defaults from the production baseline profile."""

    source = path or DEFAULT_AGENT_PROFILE_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in {source}")
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        raise ValueError(f"Missing agents object in {source}")
    orchestrator = raw.get("orchestrator_default")
    if not isinstance(orchestrator, dict):
        orchestrator = {"model": "z-ai/glm-5.3-flash", "reasoning_effort": "medium"}
    return {"agents": agents, "orchestrator_default": orchestrator}


def profile_model_ids(profile: dict[str, Any]) -> list[str]:
    """Return stable, deduplicated model IDs used by the checked-in defaults."""

    values: list[str] = []
    orchestrator = profile.get("orchestrator_default") or {}
    if isinstance(orchestrator, dict) and orchestrator.get("model"):
        values.append(str(orchestrator["model"]))
    agents = profile.get("agents") or {}
    if isinstance(agents, dict):
        for raw in agents.values():
            if isinstance(raw, dict) and raw.get("model"):
                values.append(str(raw["model"]))
    return list(dict.fromkeys(values))


def _parse_jsonl_lines(lines: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _resolved_jsonl_path(path: Path) -> Path:
    if path.exists():
        return path
    gz_path = Path(str(path) + ".gz")
    return gz_path if gz_path.exists() else path


def _read_jsonl_bytes(path: Path) -> bytes:
    actual = _resolved_jsonl_path(path)
    if not actual.exists():
        return b""
    if actual.suffix == ".gz":
        with gzip.open(actual, "rb") as handle:
            return handle.read()
    return actual.read_bytes()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = _read_jsonl_bytes(path)
    if not raw:
        return []
    return _parse_jsonl_lines(raw.decode("utf-8", errors="replace").splitlines())


def read_jsonl_tail(path: Path, *, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Read only the logical tail of a potentially huge JSONL or JSONL.GZ file."""

    if max_bytes <= 0:
        return []
    actual = _resolved_jsonl_path(path)
    if not actual.exists():
        return []
    if actual.suffix == ".gz":
        raw = _read_jsonl_bytes(actual)
        start = max(0, len(raw) - int(max_bytes))
        raw = raw[start:]
        if start:
            newline = raw.find(b"\n")
            raw = raw[newline + 1 :] if newline >= 0 else b""
    else:
        with actual.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - int(max_bytes))
            handle.seek(start)
            if start:
                handle.readline()
            raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    return _parse_jsonl_lines(text.splitlines())


def read_jsonl_since(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSONL records after an uncompressed-byte offset.

    For ``.gz`` archives the offset refers to the logical decompressed JSONL
    bytes, so a live raw stream can be atomically replaced by its gzip archive
    without invalidating an existing UI offset.
    """

    actual = _resolved_jsonl_path(path)
    if not actual.exists():
        return [], 0
    start = max(0, int(offset))
    if actual.suffix == ".gz":
        all_raw = _read_jsonl_bytes(actual)
        if start > len(all_raw):
            start = 0
        raw = all_raw[start:]
    else:
        size = actual.stat().st_size
        if start > size:
            start = 0
        if start == size:
            return [], start
        with actual.open("rb") as handle:
            handle.seek(start)
            raw = handle.read()
    if not raw:
        return [], start
    last_newline = raw.rfind(b"\n")
    if last_newline < 0:
        return [], start
    complete = raw[: last_newline + 1]
    new_offset = start + last_newline + 1
    text = complete.decode("utf-8", errors="replace")
    return _parse_jsonl_lines(text.splitlines()), new_offset


def _logical_jsonl_size(path: Path) -> int:
    actual = _resolved_jsonl_path(path)
    if not actual.exists():
        return 0
    if actual.suffix == ".gz":
        return len(_read_jsonl_bytes(actual))
    return actual.stat().st_size


def live_log_offsets(run_dir: Path) -> dict[str, int]:
    return {
        "trace": _logical_jsonl_size(run_dir / "trace.jsonl"),
        "stream": _logical_jsonl_size(run_dir / "stream.jsonl"),
    }


def load_live_run_delta(
    run_dir: Path,
    offsets: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read only newly appended trace/stream records after the previous fragment."""

    trace_events, trace_offset = read_jsonl_since(run_dir / "trace.jsonl", int(offsets.get("trace", 0) or 0))
    stream_events, stream_offset = read_jsonl_since(run_dir / "stream.jsonl", int(offsets.get("stream", 0) or 0))
    events = trace_events + stream_events
    events.sort(key=lambda ev: str(ev.get("ts") or ""))
    return events, {"trace": trace_offset, "stream": stream_offset}


def load_run_events(run_dir: Path, *, include_stream: bool = True) -> list[dict[str, Any]]:
    events = read_jsonl(run_dir / "trace.jsonl")
    if include_stream:
        events.extend(read_jsonl(run_dir / "stream.jsonl"))
    events.sort(key=lambda ev: str(ev.get("ts") or ""))
    return events


def load_live_run_events(run_dir: Path, *, stream_tail_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Load a live timeline without re-reading the entire streaming log."""

    events = read_jsonl(run_dir / "trace.jsonl")
    events.extend(read_jsonl_tail(run_dir / "stream.jsonl", max_bytes=stream_tail_bytes))
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
