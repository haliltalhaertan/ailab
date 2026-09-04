from __future__ import annotations

import gzip
import json
import re
import shutil
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lab.budget import budget_snapshot


_ACTIVE_TRACE: ContextVar["Trace | None"] = ContextVar("ailab_active_trace", default=None)
StageListener = Callable[[dict[str, Any]], None]


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
        self.experiment = experiment
        self.experiment_method: str | None = None
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
        self._stage_listener: StageListener | None = None
        self._stage_index = 0
        self._active_stages: dict[str, dict[str, Any]] = {}
        self._auto_stage_by_agent: dict[str, str] = {}
        self._theorem_stage_total: int | None = None
        self._theorem_stage_total_is_minimum = False
        self._index("run_started", experiment=experiment, started_at=self.started_at)
        _ACTIVE_TRACE.set(self)

    def set_stage_listener(self, listener: StageListener | None) -> None:
        self._stage_listener = listener

    def configure_theorem_stages(
        self,
        *,
        iterations: int,
        checkpoint_every: int,
        has_literature_agent: bool,
    ) -> int:
        count = max(0, int(iterations)) * 4
        if has_literature_agent:
            count += 1
        every = int(checkpoint_every)
        if every > 0:
            count += max(0, int(iterations)) // every
        count += 1
        self._theorem_stage_total = count
        self._theorem_stage_total_is_minimum = True
        return count

    def configure_theorem_resume_offset(
        self,
        *,
        completed_iterations: int,
        checkpoint_every: int,
    ) -> int:
        """Seed only structurally completed theorem calls before cache replay.

        Literature and partial-iteration LLM calls are deliberately not guessed:
        each real ``step_reused(agent=...)`` advances the index when replayed.
        """

        completed = max(0, int(completed_iterations))
        every = int(checkpoint_every)
        offset = 4 * completed
        if every > 0:
            offset += completed // every
        self._stage_index = offset
        return offset

    @staticmethod
    def _theorem_stage_label(step_key: str, agent: str) -> str:
        key = str(step_key or "").strip()
        if key.endswith(":json_repair"):
            base = key[: -len(":json_repair")]
            base_label = Trace._theorem_stage_label(base, agent)
            return f"{base_label} · JSON onarımı"
        if key == "literature:agent":
            return "Literatür · LiteratureScout"
        match = re.fullmatch(r"iter:(\d+):proposer", key)
        if match:
            return f"Tur {match.group(1)} · Theorist · öneri"
        match = re.fullmatch(r"iter:(\d+):verifier", key)
        if match:
            return f"Tur {match.group(1)} · VerificationEngineer · doğrulama"
        match = re.fullmatch(r"iter:(\d+):critic", key)
        if match:
            return f"Tur {match.group(1)} · AdversarialCritic · eleştiri"
        match = re.fullmatch(r"iter:(\d+):manager", key)
        if match:
            return f"Tur {match.group(1)} · ResearchManager · karar"
        match = re.fullmatch(r"iter:(\d+):tool:plan:(\d+)", key)
        if match:
            return f"Tur {match.group(1)} · CodeExperimentAgent · adım {match.group(2)}"
        match = re.fullmatch(r"iter:(\d+):checkpoint_audit", key)
        if match:
            return f"Tur {match.group(1)} · Denetim"
        if key == "final:audit":
            return "Final denetim"
        return f"{agent} · {key}"

    def _index(self, event: str, **data: Any) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            **data,
        }
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_trace(self, event: dict[str, Any]) -> None:
        self._trace_handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _notify_stage(self, event: dict[str, Any]) -> None:
        if self._stage_listener is not None:
            self._stage_listener(dict(event))

    def _write_stage(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
        self._write_trace(event)
        self._notify_stage(event)
        return event

    def _auto_stage_for_agent_start(self, data: dict[str, Any]) -> None:
        step_key = str(data.get("step_key") or "").strip()
        if not step_key or step_key in self._active_stages:
            return
        self._stage_index += 1
        agent = str(data.get("agent") or "Agent")
        method = str(self.experiment_method or "theorem_lab")
        theorem_auto = method == "theorem_lab"
        stage = {
            "method": method,
            "label": self._theorem_stage_label(step_key, agent) if theorem_auto else f"{agent} · {step_key}",
            "index": self._stage_index,
            "total": self._theorem_stage_total if theorem_auto else None,
            "total_is_minimum": self._theorem_stage_total_is_minimum if theorem_auto else False,
            "agent": agent,
            "model": data.get("model"),
            "reasoning_effort": data.get("reasoning_effort"),
            "step_key": step_key,
            "auto": True,
        }
        event = self._write_stage("stage", stage)
        self._active_stages[step_key] = event
        self._auto_stage_by_agent[agent] = step_key

    def _auto_stage_end_for_llm_call(self, data: dict[str, Any]) -> None:
        agent = str(data.get("agent") or "Agent")
        step_key = self._auto_stage_by_agent.get(agent)
        if not step_key:
            return
        start = self._active_stages.get(step_key)
        if not start or start.get("auto") is not True:
            return
        end = {
            "method": start.get("method") or self.experiment_method or "theorem_lab",
            "label": start.get("label"),
            "index": start.get("index"),
            "total": start.get("total"),
            "total_is_minimum": bool(start.get("total_is_minimum")),
            "agent": agent,
            "step_key": step_key,
            "total_tokens": int(data.get("total_tokens", 0) or 0),
            "reasoning_tokens": int(data.get("reasoning_tokens", 0) or 0),
            "cost_usd": data.get("cost_usd"),
            "latency_s": float(data.get("latency_s", 0.0) or 0.0),
            "finish_reason": data.get("finish_reason"),
            "truncated": bool(data.get("truncated")),
            "requested_max_tokens": data.get("requested_max_tokens"),
            "budget": data.get("budget") or {},
            "auto": True,
        }
        self._write_stage("stage_end", end)
        self._active_stages.pop(step_key, None)
        self._auto_stage_by_agent.pop(agent, None)

    def _flush_stream(self, *, force: bool = False) -> None:
        if not self._stream_buffers:
            return
        now = time.monotonic()
        if not force and now - self._stream_last_flush < self.STREAM_FLUSH_S:
            if sum(int(v.get("chars", 0)) for v in self._stream_buffers.values()) < self.STREAM_FLUSH_CHARS:
                return
        for payload in list(self._stream_buffers.values()):
            event = {
                "ts": payload["ts"], "type": "agent_stream", "agent": payload["agent"],
                "model": payload["model"], "reasoning_effort": payload.get("reasoning_effort"),
                "step_key": payload["step_key"], "channel": payload["channel"],
                "delta": payload["delta"], "batch_parts": payload["parts"],
            }
            self._stream_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._stream_handle.flush()
        self._stream_buffers.clear()
        self._stream_last_flush = now

    def _buffer_stream(self, data: dict[str, Any]) -> None:
        channel = str(data.get("channel") or "")
        delta = data.get("delta")
        if not isinstance(delta, str):
            self._flush_stream(force=True)
            event = {"ts": datetime.now(timezone.utc).isoformat(), "type": "agent_stream", **data, "batch_parts": 1}
            self._stream_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._stream_handle.flush()
            return
        key = (str(data.get("agent") or ""), str(data.get("model") or ""), str(data.get("step_key") or ""), channel)
        payload = self._stream_buffers.get(key)
        if payload is None:
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(), "agent": key[0], "model": key[1],
                "step_key": key[2], "channel": channel, "reasoning_effort": data.get("reasoning_effort"),
                "delta": "", "parts": 0, "chars": 0,
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

        if event_type == "stage":
            event = self._write_stage("stage", data)
            step_key = str(data.get("step_key") or "").strip()
            if step_key:
                self._active_stages[step_key] = event
            return
        if event_type == "stage_end":
            self._write_stage("stage_end", data)
            step_key = str(data.get("step_key") or "").strip()
            if step_key:
                active = self._active_stages.pop(step_key, None)
                if active:
                    agent = str(active.get("agent") or "")
                    if self._auto_stage_by_agent.get(agent) == step_key:
                        self._auto_stage_by_agent.pop(agent, None)
            return
        if event_type == "agent_start":
            self._auto_stage_for_agent_start(data)

        if (
            event_type == "step_reused"
            and self.experiment_method == "theorem_lab"
            and str(data.get("agent") or "").strip()
        ):
            self._stage_index += 1
            data = dict(data)
            agent = str(data.get("agent") or "Agent")
            step_key = str(data.get("step_key") or "")
            data.update(
                {
                    "index": self._stage_index,
                    "total": self._theorem_stage_total,
                    "total_is_minimum": self._theorem_stage_total_is_minimum,
                    "label": self._theorem_stage_label(step_key, agent),
                }
            )

        event = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
        self._write_trace(event)
        if event_type == "project_context":
            self.project_id = str(data.get("project_id") or "") or None
            self.project_uuid = str(data.get("project_uuid") or "") or None
            if data.get("experiment_method"):
                self.experiment_method = str(data["experiment_method"])
            if self.experiment_method == "theorem_lab" and data.get("iterations") is not None:
                self.configure_theorem_stages(
                    iterations=int(data.get("iterations") or 0),
                    checkpoint_every=int(data.get("checkpoint_every") or 0),
                    has_literature_agent=bool(data.get("has_literature_agent")),
                )
            self._index(
                "run_context", project_id=self.project_id, project_uuid=self.project_uuid,
                title=data.get("title"), experiment=data.get("experiment"),
            )
        elif event_type == "llm_call":
            self._auto_stage_end_for_llm_call(data)

    def agent_call(self, agent: str, model: str, temperature: float, messages: list[dict], response) -> None:
        exact_messages = getattr(response, "request_messages", None) or messages
        total_tokens = int(response.prompt_tokens or 0) + int(response.completion_tokens or 0)
        budget = budget_snapshot(agent, model, total_tokens)
        finish_reason = getattr(response, "finish_reason", None)
        truncated = str(finish_reason or "").lower() == "length"
        self.log(
            "llm_call", agent=agent, model=model, temperature=temperature,
            reasoning_effort=getattr(response, "requested_reasoning_effort", None),
            messages=exact_messages, output=response.content,
            provider_reasoning=getattr(response, "provider_reasoning", ""),
            reasoning_details=getattr(response, "reasoning_details", None),
            prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
            reasoning_tokens=getattr(response, "reasoning_tokens", 0),
            cached_tokens=getattr(response, "cached_tokens", 0),
            total_tokens=total_tokens,
            cost_usd=getattr(response, "cost_usd", None), latency_s=response.latency_s,
            finish_reason=finish_reason,
            truncated=truncated,
            requested_max_tokens=getattr(response, "requested_max_tokens", None),
            budget=budget,
        )
        if budget.get("over_budget"):
            self.log(
                "unusually_expensive_call",
                agent=agent,
                model=model,
                total_tokens=total_tokens,
                expected_min=budget.get("expected_min"),
                expected_max=budget.get("expected_max"),
            )

    def close(self) -> Path:
        if self.closed:
            return self.run_dir / "summary.json"
        self._flush_stream(force=True)
        self._trace_handle.flush()

        def new_total() -> dict[str, Any]:
            return {
                "calls": 0, "models": [], "reasoning_efforts": [], "prompt_tokens": 0,
                "completion_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0,
                "total_tokens": 0, "cost_usd": 0.0, "cost_available_calls": 0, "latency_s": 0.0,
                "truncated_calls": 0, "over_budget_calls": 0,
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
                if ev.get("truncated"):
                    t["truncated_calls"] += 1
                if bool((ev.get("budget") or {}).get("over_budget")):
                    t["over_budget_calls"] += 1

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
            "run_id": self.run_id, "project_id": self.project_id, "project_uuid": self.project_uuid,
            "started_at": self.started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_time_s": round(time.perf_counter() - self._started_perf, 3),
            "llm_latency_s": round(llm_latency, 3), "agents": agents, "total_calls": total_calls,
            "total_prompt_tokens": total_prompt, "total_completion_tokens": total_completion,
            "total_reasoning_tokens": total_reasoning, "total_cached_tokens": total_cached,
            "total_tokens": total_tokens, "total_cost_usd": round(total_cost, 8),
            "cost_available_calls": cost_available_calls,
            "cost_complete": cost_available_calls == total_calls if total_calls else True,
            "event_count": event_count,
            "stream_bytes": self.stream_path.stat().st_size if self.stream_path.exists() else 0,
        }
        out = self.run_dir / "summary.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._index(
            "run_finished", project_id=self.project_id, project_uuid=self.project_uuid,
            summary=str(out), total_tokens=total_tokens, total_cost_usd=round(total_cost, 8),
        )
        self.closed = True
        self._trace_handle.close()
        self._stream_handle.close()
        return out

    def compress_stream(self) -> Path | None:
        if not self.closed:
            raise RuntimeError("Trace must be closed before stream compression")
        raw = self.stream_path
        gz_path = Path(str(raw) + ".gz")
        if not raw.exists():
            return gz_path if gz_path.exists() else None
        tmp = Path(str(gz_path) + ".tmp")
        try:
            with raw.open("rb") as source, gzip.open(tmp, "wb", compresslevel=6) as target:
                shutil.copyfileobj(source, target)
            tmp.replace(gz_path)
            raw.unlink()
        finally:
            tmp.unlink(missing_ok=True)
        return gz_path
