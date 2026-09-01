from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ACTIVE_TRACE: ContextVar["Trace | None"] = ContextVar("ailab_active_trace", default=None)


def get_active_trace() -> "Trace | None":
    trace = _ACTIVE_TRACE.get()
    if trace is None or trace.closed:
        return None
    return trace


class Trace:
    """Append-only run trace with a separate buffered streaming channel."""

    STREAM_FLUSH_S = 0.20
    STREAM_FLUSH_CHARS = 4096

    def __init__(self, experiment: str, out_dir: str | Path = "runs"):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_nonce = uuid.uuid4().hex[:10]
        self.run_id = f"{stamp}_{run_nonce}_{experiment}"
        self.out_dir = Path(out_dir)
        self.run_dir = self.out_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.path = self.run_dir / "trace.jsonl"
        self.stream_path = self.run_dir / "stream.jsonl"
        self.index_path = self.out_dir / "index.jsonl"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_perf = time.perf_counter()
        self.closed = False
        self.project_id: str | None = None
        self.project_uuid: str | None = None
        self._trace_handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._stream_handle = self.stream_path.open("a", encoding="utf-8", buffering=1)
        self._stream_buffers: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._stream_last_flush = time.monotonic()
        self._index("run_started", experiment=experiment, started_at=self.started_at)
        _ACTIVE_TRACE.set(self)

    def _index(self, event: str, **data: Any) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "run_id": self.run_id, "run_dir": str(self.run_dir), **data}
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_trace(self, event: dict[str, Any]) -> None:
        self._trace_handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _flush_stream(self, *, force: bool = False) -> None:
        if not self._stream_buffers:
            return
        now = time.monotonic()
        if not force and now - self._stream_last_flush < self.STREAM_FLUSH_S:
            if sum(int(v.get("chars", 0)) for v in self._stream_buffers.values()) < self.STREAM_FLUSH_CHARS:
                return
        for payload in list(self._stream_buffers.values()):
            event = {
                "ts": payload["ts"],
                "type": "agent_stream",
                "agent": payload["agent"],
                "model": payload["model"],
                "reasoning_effort": payload.get("reasoning_effort"),
                "step_key": payload["step_key"],
                "channel": payload["channel"],
                "delta": payload["delta"],
                "batch_parts": payload["parts"],
            }
            self._stream_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._stream_handle.flush()
        self._stream_buffers.clear()
        self._stream_last_flush = now

    def _buffer_stream(self, data: dict[str, Any]) -> None:
        channel = str(data.get("channel") or "")
        delta = data.get("delta")
        # Structured reasoning details are not safely concatenable; write them as
        # one stream event immediately rather than inflating trace.jsonl.
        if not isinstance(delta, str):
            self._flush_stream(force=True)
            event = {"ts": datetime.now(timezone.utc).isoformat(), "type": "agent_stream", **data, "batch_parts": 1}
            self._stream_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._stream_handle.flush()
            return
        key = (
            str(data.get("agent") or ""),
            str(data.get("model") or ""),
            str(data.get("step_key") or ""),
            channel,
        )
        payload = self._stream_buffers.get(key)
        if payload is None:
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent": key[0],
                "model": key[1],
                "step_key": key[2],
                "channel": channel,
                "reasoning_effort": data.get("reasoning_effort"),
                "delta": "",
                "parts": 0,
                "chars": 0,
            }
            self._stream_buffers[key] = payload
        payload["delta"] += delta
        payload["parts"] += 1
        payload["chars"] += len(delta)
        self._flush_stream()

    def log(self, event_type: str, **data: Any) -> None:
        if self.closed:
            return
        if event_type == "agent_stream":
            self._buffer_stream(data)
            return
        self._flush_stream()
        event = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
        self._write_trace(event)
        if event_type == "project_context":
            self.project_id = str(data.get("project_id") or "") or None
            self.project_uuid = str(data.get("project_uuid") or "") or None
            self._index(
                "run_context",
                project_id=self.project_id,
                project_uuid=self.project_uuid,
                title=data.get("title"),
                experiment=data.get("experiment"),
            )

    def agent_call(self, agent: str, model: str, temperature: float, messages: list[dict], response) -> None:
        exact_messages = getattr(response, "request_messages", None) or messages
        self.log(
            "llm_call",
            agent=agent,
            model=model,
            temperature=temperature,
            reasoning_effort=getattr(response, "requested_reasoning_effort", None),
            messages=exact_messages,
            output=response.content,
            provider_reasoning=getattr(response, "provider_reasoning", ""),
            reasoning_details=getattr(response, "reasoning_details", None),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            reasoning_tokens=getattr(response, "reasoning_tokens", 0),
            cached_tokens=getattr(response, "cached_tokens", 0),
            total_tokens=response.prompt_tokens + response.completion_tokens,
            cost_usd=getattr(response, "cost_usd", None),
            latency_s=response.latency_s,
        )

    def close(self) -> Path:
        if self.closed:
            return self.run_dir / "summary.json"
        self._flush_stream(force=True)
        self._trace_handle.flush()

        def new_total() -> dict[str, Any]:
            return {
                "calls": 0,
                "models": [],
                "reasoning_efforts": [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cost_available_calls": 0,
                "latency_s": 0.0,
            }

        totals: dict[str, dict[str, Any]] = defaultdict(new_total)
        event_count = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_count += 1
                if ev.get("type") != "llm_call":
                    continue
                t = totals[str(ev.get("agent") or "Agent")]
                t["calls"] += 1
                if ev.get("model") and ev["model"] not in t["models"]:
                    t["models"].append(ev["model"])
                effort = ev.get("reasoning_effort")
                if effort is not None and effort not in t["reasoning_efforts"]:
                    t["reasoning_efforts"].append(effort)
                for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "total_tokens"):
                    t[key] += int(ev.get(key, 0) or 0)
                if ev.get("cost_usd") is not None:
                    t["cost_usd"] += float(ev["cost_usd"])
                    t["cost_available_calls"] += 1
                t["latency_s"] += float(ev.get("latency_s", 0.0) or 0.0)

        agents = dict(totals)
        total_calls = sum(t["calls"] for t in agents.values())
        cost_available_calls = sum(t["cost_available_calls"] for t in agents.values())
        total_prompt = sum(t["prompt_tokens"] for t in agents.values())
        total_completion = sum(t["completion_tokens"] for t in agents.values())
        total_reasoning = sum(t["reasoning_tokens"] for t in agents.values())
        total_cached = sum(t["cached_tokens"] for t in agents.values())
        total_tokens = sum(t["total_tokens"] for t in agents.values())
        total_cost = sum(t["cost_usd"] for t in agents.values())
        llm_latency = sum(t["latency_s"] for t in agents.values())
        for t in agents.values():
            t["cost_usd"] = round(t["cost_usd"], 8)
            t["latency_s"] = round(t["latency_s"], 3)
            t["cost_complete"] = t["cost_available_calls"] == t["calls"]
        summary = {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "project_uuid": self.project_uuid,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_time_s": round(time.perf_counter() - self._started_perf, 3),
            "llm_latency_s": round(llm_latency, 3),
            "agents": agents,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_reasoning_tokens": total_reasoning,
            "total_cached_tokens": total_cached,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 8),
            "cost_available_calls": cost_available_calls,
            "cost_complete": cost_available_calls == total_calls if total_calls else True,
            "event_count": event_count,
            "stream_bytes": self.stream_path.stat().st_size if self.stream_path.exists() else 0,
        }
        out = self.run_dir / "summary.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._index(
            "run_finished",
            project_id=self.project_id,
            project_uuid=self.project_uuid,
            summary=str(out),
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 8),
        )
        self.closed = True
        self._trace_handle.close()
        self._stream_handle.close()
        return out
