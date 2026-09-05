from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from lab.agent import Agent
from lab.client import next_lower_supported_effort
from lab.code_experiment import (
    CODE_EXPERIMENT_SYSTEM_PROMPT,
    CodeExperimentRunner,
    GuardedExperimentWorkspace,
    WorkspaceActionResult,
    infrastructure_failure,
    top_level_bound_names,
)
from lab.code_experiment_settings import load_code_experiment_settings, load_code_experiment_settings_from_dict
from lab.evidence import evidence_from_tool_result, validate_evidence_binding
from lab.integrity import content_fingerprint, sha256_file
from lab.iteration_control import consume_iteration_restart
from lab.json_io import StructuredOutputError, parse_json_object, parse_truncated_object_prefix, repair_instruction
from lab.literature import LiteratureClient, LiteratureSearchEmpty, Paper
from lab.prompts import checkpoint_prompt, critic_prompt, literature_prompt, manager_prompt, proposal_prompt, verifier_prompt
from lab.research_contract import ResearchContract
from lab.reasoning_settings import get_reasoning_effort
from lab.research_protocol import (
    evaluate_manager_target_proposal,
    ledger_records,
    pilot_evidence_by_target,
    pilot_prompt_block,
    selectable_target_ids,
)
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused, ResearchStopped, RunController, atomic_json, now_iso, retryable
from lab.status_guard import choose_status
from lab.step_store import StepStore
from lab.tool_registry import ToolRegistry
from lab.tools import LeanTool, ResearchToolbox, ToolResult
from lab.trace import Trace


_RETRY_INSTRUCTION = (
    "The previous response ended at the provider token limit before producing usable JSON. "
    "Keep the reasoning focused and return the requested JSON directly. "
    "Do not repeat long arithmetic, trajectories, enumeration, or calculations that deterministic tools can perform."
)

def _is_truncated_empty_record(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        str(value.get("status") or "") == "TRUNCATED_EMPTY"
        or (
            str(value.get("status") or "") == "COMPLETE"
            and bool(value.get("truncated"))
            and str(value.get("finish_reason") or "").casefold() == "length"
            and not str(value.get("content") or "").strip()
        )
    )

def _response_meta(response: Any, content: str) -> dict[str, Any]:
    return {
        "finish_reason": getattr(response, "finish_reason", None),
        "truncated": str(getattr(response, "finish_reason", None) or "").casefold() == "length",
        "requested_max_tokens": getattr(response, "requested_max_tokens", None),
        "model_max_completion_tokens": getattr(response, "model_max_completion_tokens", None),
        "max_tokens_source": getattr(response, "max_tokens_source", "provider_default"),
        "catalog_source": getattr(response, "catalog_source", "unavailable"),
        "reasoning_effort_requested": getattr(response, "requested_reasoning_effort", None),
        "reasoning_effort_sent": getattr(response, "reasoning_effort_sent", None),
        "effort_resolution": getattr(response, "effort_resolution", "provider_default"),
        "reasoning_max_tokens_sent": getattr(response, "reasoning_max_tokens_sent", None),
        "model_default_reasoning_effort": getattr(response, "model_default_reasoning_effort", None),
        "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(response, "reasoning_tokens", 0) or 0),
        "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
        "cost_usd": getattr(response, "cost_usd", None),
        "latency_s": float(getattr(response, "latency_s", 0.0) or 0.0),
        "answer_chars": len(str(content or "")),
    }

def _cached_meta(cached: dict[str, Any]) -> dict[str, Any]:
    raw = cached.get("call_meta")
    if isinstance(raw, dict):
        return dict(raw)
    content = str(cached.get("content") or "")
    return {
        "finish_reason": cached.get("finish_reason"),
        "truncated": bool(cached.get("truncated")),
        "requested_max_tokens": cached.get("requested_max_tokens"),
        "model_max_completion_tokens": cached.get("model_max_completion_tokens"),
        "max_tokens_source": cached.get("max_tokens_source", "provider_default"),
        "catalog_source": cached.get("catalog_source", "unavailable"),
        "reasoning_effort_requested": cached.get("reasoning_effort"),
        "reasoning_effort_sent": cached.get("reasoning_effort_sent"),
        "effort_resolution": cached.get("effort_resolution", "legacy_unknown"),
        "reasoning_max_tokens_sent": cached.get("reasoning_max_tokens_sent"),
        "model_default_reasoning_effort": cached.get("model_default_reasoning_effort"),
        "prompt_tokens": int(cached.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(cached.get("completion_tokens", 0) or 0),
        "reasoning_tokens": int(cached.get("reasoning_tokens", 0) or 0),
        "total_tokens": int(cached.get("total_tokens", 0) or 0),
        "cost_usd": cached.get("cost_usd"),
        "latency_s": float(cached.get("latency_s", 0.0) or 0.0),
        "answer_chars": len(content),
        "legacy_telemetry_incomplete": not isinstance(raw, dict),
    }

def _retry_event_payload(meta: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "answer_chars",
        "cost_usd",
        "finish_reason",
        "reasoning_effort_requested",
        "reasoning_effort_sent",
        "requested_max_tokens",
        "model_max_completion_tokens",
        "max_tokens_source",
        "catalog_source",
        "model_default_reasoning_effort",
    )
    return {key: meta.get(key) for key in keys}


@dataclass
class IterationOutcome:
    item_id: str
    decision: str
    status: str
    next_task: str


def paper_context(papers: list[Paper]) -> str:
    if not papers:
        return "(retrieval returned no usable candidate records; novelty is INCONCLUSIVE)"
    rows = []
    for i, paper in enumerate(papers, 1):
        authors = ", ".join(paper.authors[:3])
        rows.append(f"{i}. {paper.title} ({paper.year or '?'}) — {authors} — {paper.url}")
    return "\n".join(rows)


class TheoremResearchLab:
    """Single production theorem workflow with machine-gated evidence binding."""

    PARTIAL_RESUME_CHAR_LIMIT = 60_000
    PARTIAL_FLUSH_INTERVAL_S = 0.25
    PARTIAL_FLUSH_CHARS = 1024

    def __init__(
        self,
        trace: Trace,
        state: ResearchState,
        literature: LiteratureClient | None = None,
        toolbox: ResearchToolbox | None = None,
        *,
        max_retries: int = 3,
        code_experiment_steps: int | None = None,
        code_experiment_settings_override: dict[str, Any] | None = None,
    ):
        self.trace = trace
        self.state = state
        self.literature = literature or LiteratureClient()
        self.step_store = StepStore(state.root)
        self.controller = RunController(state.root, trace, max_retries=max_retries)
        if toolbox is None:
            toolbox = ResearchToolbox(lean_root=state.root / "formal")
        elif isinstance(getattr(toolbox, "lean", None), LeanTool):
            timeout_s = int(getattr(toolbox.lean, "timeout_s", 120))
            toolbox.lean = LeanTool(state.root / "formal", timeout_s=timeout_s)
        self.toolbox = toolbox
        self.registry = ToolRegistry(self.toolbox)
        base_settings = load_code_experiment_settings()
        if code_experiment_settings_override:
            base_settings = load_code_experiment_settings_from_dict({**base_settings, **code_experiment_settings_override})
        self.code_settings = base_settings
        steps = int(code_experiment_steps or base_settings.get("max_steps", 8))
        self.code_agent: Agent | None = None
        self.code_workspace = GuardedExperimentWorkspace(
            self.state.root / "workspace",
            timeout_s=int(base_settings.get("timeout_s", 60)),
            memory_limit_mb=int(base_settings.get("memory_limit_mb", 768)),
            max_output_bytes=int(base_settings.get("max_output_mb", 4)) * 1024 * 1024,
            pid_limit=int(base_settings.get("pid_limit", 8)),
            cpu_limit=float(base_settings.get("cpu_limit", 1.0)),
            cancel_check=lambda: self.controller.stop_path.exists(),
            container_engine=str(base_settings.get("container_engine") or "") or None,
            container_image=str(base_settings.get("container_image") or "python:3.12-slim"),
        )
        self.code_runner = CodeExperimentRunner(self.code_workspace, self.trace, max_steps=steps)
        self.registry.register("code_experiment", self._code_experiment_placeholder)
        self._literature_reliable = False
        self._active_iteration: int | None = None
        self._active_item_id = ""
        self._active_claim_hash = ""
        self._active_claim_sha256 = ""
        self._llm_step_meta: dict[str, dict[str, Any]] = {}

    @property
    def runtime_path(self):
        return self.controller.runtime_path

    @property
    def config_path(self):
        return self.controller.config_path

    @property
    def stop_path(self):
        return self.controller.stop_path

    def _runtime(self) -> dict[str, Any]:
        return self.controller.runtime()

    def _set_runtime(self, **updates: Any) -> dict[str, Any]:
        return self.controller.set_runtime(**updates)

    def _check_stop(self) -> None:
        self.controller.check_stop()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        return self.step_store.get_step(key)

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        self.step_store.put_step(key, value)

    def _cache_delete(self, key: str) -> None:
        self.step_store.delete_step(key)

    def _partial_get(self, key: str) -> dict[str, Any] | None:
        return self.step_store.get_partial(key)

    def _partial_put(self, key: str, value: dict[str, Any]) -> None:
        self.step_store.put_partial(key, value)

    def _partial_clear(self, key: str) -> None:
        self.step_store.clear_partial(key)

    @staticmethod
    def _code_experiment_placeholder(request: dict[str, Any]) -> ToolResult:
        return ToolResult(False, "code_experiment", error="code_experiment requires theorem-run context")

    def _save_config(
        self,
        problem: str,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        agents: dict[str, Agent],
        *,
        allow_discovery_without_pilot: bool = False,
    ) -> None:
        if self.code_agent is not None:
            agents = {**agents, "CodeExperimentAgent": self.code_agent}
        contract = ResearchContract.load_optional(self.state.root)
        payload = {
            "config_version": 3,
            "problem": problem,
            "iterations": int(iterations),
            "literature_query": literature_query,
            "checkpoint_every": int(checkpoint_every),
            "allow_discovery_without_pilot": bool(allow_discovery_without_pilot),
            "contract_hash": contract.contract_hash if contract is not None and contract.frozen else "",
            "agents": {
                role: {
                    "name": agent.name,
                    "system_prompt": agent.system_prompt,
                    "model": agent.model,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                    "reasoning_effort": agent.reasoning_effort,
                }
                for role, agent in agents.items()
            },
            "code_experiment": dict(self.code_settings),
            "saved_at": now_iso(),
        }
        atomic_json(self.config_path, payload)

    def _llm_fingerprint(self, agent: Agent, prompt: str) -> str:
        return content_fingerprint(
            "llm_step:v3",
            {
                "agent": agent.name,
                "system_prompt": agent.system_prompt,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
                "reasoning_effort": agent.reasoning_effort,
                "prompt": prompt,
            },
        )

    @staticmethod
    def _tail(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return "[... earlier visible work truncated ...]\n" + text[-limit:]

    def _resume_messages(self, prompt: str, partial: dict[str, Any] | None, *, current_model: str | None) -> list[dict[str, Any]]:
        if not partial:
            return [{"role": "user", "content": prompt}]
        reasoning = str(partial.get("reasoning") or "")
        content = str(partial.get("content") or "")
        details = partial.get("reasoning_details")
        same_model = str(partial.get("model") or "") == str(current_model or "")
        if details and same_model:
            assistant: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                assistant["reasoning"] = reasoning
            assistant["reasoning_details"] = details
            return [
                {"role": "user", "content": prompt},
                assistant,
                {
                    "role": "user",
                    "content": "The previous response was interrupted. Continue from the provider-visible structured reasoning above, verify it, and return a COMPLETE final response in the original requested format.",
                },
            ]
        limit = max(2_000, int(self.PARTIAL_RESUME_CHAR_LIMIT))
        context = (
            "\n\n--- INTERRUPTED STEP: SOFT RESUME CONTEXT ---\n"
            f"Previous model: {partial.get('model', '')}\n"
            "Structured provider reasoning is replayed only when the model is unchanged. "
            "Reuse the visible text below, check it, and return a COMPLETE final response.\n\n"
            f"PARTIAL REASONING:\n{self._tail(reasoning, limit // 2) or '(none)'}\n\n"
            f"PARTIAL CONTENT:\n{self._tail(content, limit // 2) or '(none)'}\n"
            "--- END SOFT RESUME CONTEXT ---"
        )
        return [{"role": "user", "content": prompt + context}]

    def _persist_partial(
        self,
        *,
        agent: Agent,
        step_key: str,
        prompt: str,
        fingerprint: str,
        buffers: dict[str, Any],
        attempt: int,
        force: bool = False,
    ) -> None:
        now_mono = time.monotonic()
        dirty = int(buffers.get("dirty_chars", 0) or 0)
        last_flush = float(buffers.get("last_flush", 0.0) or 0.0)
        if not force and dirty < self.PARTIAL_FLUSH_CHARS and now_mono - last_flush < self.PARTIAL_FLUSH_INTERVAL_S:
            return
        if not buffers.get("reasoning") and not buffers.get("content") and not buffers.get("reasoning_details"):
            return
        payload = {
            "status": "PARTIAL",
            "fingerprint": fingerprint,
            "agent": agent.name,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "original_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "reasoning": str(buffers.get("reasoning") or ""),
            "reasoning_details": buffers.get("reasoning_details") or None,
            "content": str(buffers.get("content") or ""),
            "attempt": int(attempt),
            "updated_at": now_iso(),
        }
        self._partial_put(step_key, payload)
        buffers["last_flush"] = now_mono
        buffers["dirty_chars"] = 0

    def _stream_callback(self, agent: Agent, step_key: str, prompt: str, fingerprint: str, buffers: dict[str, Any], attempt: int):
        def callback(channel: str, delta: Any) -> None:
            if isinstance(delta, str) and channel in {"reasoning", "content"}:
                buffers[channel] = str(buffers.get(channel) or "") + delta
                buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(delta)
            elif channel == "reasoning_details" and delta:
                details = buffers.setdefault("reasoning_details", [])
                details.extend(delta if isinstance(delta, list) else [delta])
                try:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(json.dumps(delta, ensure_ascii=False))
                except Exception:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + 1
            if channel in {"reasoning", "content", "reasoning_details"}:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt)
            self.trace.log("agent_stream", agent=agent.name, model=agent.model, reasoning_effort=agent.reasoning_effort, step_key=step_key, channel=channel, delta=delta)
            try:
                self._check_stop()
            except ResearchStopped:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt, force=True)
                raise
        return callback

    def _load_cached_llm_step(
        self,
        *,
        cached: dict[str, Any],
        agent: Agent,
        step_key: str,
        fingerprint: str,
    ) -> str | None:
        status = str(cached.get("status") or "")
        same_fingerprint = cached.get("fingerprint") == fingerprint
        truncated_empty = _is_truncated_empty_record(cached)
        legacy_empty = status == "COMPLETE" and truncated_empty
        same_model = not cached.get("model") or str(cached.get("model")) == str(agent.model or "")

        if truncated_empty and (same_fingerprint or (legacy_empty and same_model)):
            self._partial_clear(step_key)
            meta = _cached_meta(cached)
            self._llm_step_meta[step_key] = meta
            self.trace.log(
                "truncated_empty_reused",
                step_key=step_key,
                agent=agent.name,
                model=cached.get("model"),
                fingerprint=fingerprint,
                cached_fingerprint=cached.get("fingerprint"),
                legacy=legacy_empty,
                fingerprint_match=same_fingerprint,
            )
            return ""

        if status == "COMPLETE" and same_fingerprint:
            self._partial_clear(step_key)
            if cached.get("model") and cached.get("model") != agent.model:
                self.trace.log(
                    "model_override_reused_completed_step",
                    step_key=step_key,
                    cached_model=cached.get("model"),
                    current_model=agent.model,
                )
            self._llm_step_meta[step_key] = _cached_meta(cached)
            self.trace.log(
                "step_reused",
                step_key=step_key,
                agent=agent.name,
                model=cached.get("model"),
                fingerprint=fingerprint,
            )
            return str(cached.get("content", ""))

        if status in {"COMPLETE", "TRUNCATED_EMPTY"}:
            self.trace.log("cache_fingerprint_miss", step_key=step_key, kind="llm")
            self._cache_delete(step_key)
        return None

    def _call(self, agent: Agent, prompt: str, step_key: str) -> str:
        fingerprint = self._llm_fingerprint(agent, prompt)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict):
            reused = self._load_cached_llm_step(
                cached=cached,
                agent=agent,
                step_key=step_key,
                fingerprint=fingerprint,
            )
            if reused is not None:
                return reused

        partial = self._partial_get(step_key)
        if partial and partial.get("fingerprint") != fingerprint:
            self.trace.log("partial_fingerprint_miss", step_key=step_key)
            self._partial_clear(step_key)
            partial = None

        self._check_stop()
        self._set_runtime(current_step=step_key)
        for attempt in range(1, self.controller.max_retries + 1):
            partial = self._partial_get(step_key)
            messages = self._resume_messages(prompt, partial, current_model=agent.model)
            buffers: dict[str, Any] = {
                "reasoning": str((partial or {}).get("reasoning") or ""),
                "reasoning_details": list((partial or {}).get("reasoning_details") or []),
                "content": str((partial or {}).get("content") or ""),
                "dirty_chars": 0,
                "last_flush": time.monotonic(),
            }
            if partial:
                self.trace.log(
                    "partial_resume_loaded",
                    step_key=step_key,
                    agent=agent.name,
                    previous_model=partial.get("model"),
                    current_model=agent.model,
                    structured=bool(partial.get("reasoning_details") and partial.get("model") == agent.model),
                    reasoning_chars=len(str(partial.get("reasoning") or "")),
                    content_chars=len(str(partial.get("content") or "")),
                )
            self.trace.log(
                "agent_start",
                agent=agent.name,
                model=agent.model,
                temperature=agent.temperature,
                reasoning_effort=agent.reasoning_effort,
                system_prompt=agent.system_prompt,
                prompt=prompt,
                step_key=step_key,
                attempt=attempt,
                soft_resume=bool(partial),
                fingerprint=fingerprint,
            )
            try:
                content, response = agent.respond(
                    messages,
                    stream_callback=self._stream_callback(
                        agent,
                        step_key,
                        prompt,
                        fingerprint,
                        buffers,
                        attempt,
                    ),
                )
                self.trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
                meta = _response_meta(response, content)
                self._llm_step_meta[step_key] = meta
                truncated_empty = bool(meta["truncated"] and not str(content or "").strip())
                cache_status = "TRUNCATED_EMPTY" if truncated_empty else "COMPLETE"
                self._cache_put(
                    step_key,
                    {
                        "status": cache_status,
                        "fingerprint": fingerprint,
                        "content": content,
                        "model": response.model,
                        "reasoning_effort": agent.reasoning_effort,
                        "finish_reason": meta["finish_reason"],
                        "truncated": meta["truncated"],
                        "requested_max_tokens": meta["requested_max_tokens"],
                        "model_max_completion_tokens": meta["model_max_completion_tokens"],
                        "max_tokens_source": meta["max_tokens_source"],
                        "catalog_source": meta["catalog_source"],
                        "reasoning_effort_sent": meta["reasoning_effort_sent"],
                        "effort_resolution": meta["effort_resolution"],
                        "reasoning_max_tokens_sent": meta["reasoning_max_tokens_sent"],
                        "model_default_reasoning_effort": meta["model_default_reasoning_effort"],
                        "call_meta": meta,
                        "completed_at": now_iso(),
                        "soft_resumed": bool(partial),
                    },
                )
                if truncated_empty:
                    self.trace.log(
                        "truncated_empty_persisted",
                        step_key=step_key,
                        agent=agent.name,
                        fingerprint=fingerprint,
                        call=_retry_event_payload(meta),
                    )
                self._partial_clear(step_key)
                return content
            except ResearchStopped:
                self._persist_partial(
                    agent=agent,
                    step_key=step_key,
                    prompt=prompt,
                    fingerprint=fingerprint,
                    buffers=buffers,
                    attempt=attempt,
                    force=True,
                )
                raise
            except Exception as exc:
                self._persist_partial(
                    agent=agent,
                    step_key=step_key,
                    prompt=prompt,
                    fingerprint=fingerprint,
                    buffers=buffers,
                    attempt=attempt,
                    force=True,
                )
                do_retry = retryable(exc) and attempt < self.controller.max_retries
                self.trace.log(
                    "agent_error",
                    agent=agent.name,
                    model=agent.model,
                    step_key=step_key,
                    attempt=attempt,
                    retrying=do_retry,
                    error=repr(exc),
                )
                if not do_retry:
                    raise ResearchPaused(f"{agent.name} / {agent.model} adımında hata: {exc}") from exc
                wait_s = min(2 ** (attempt - 1), 8)
                self.trace.log(
                    "agent_retry",
                    agent=agent.name,
                    step_key=step_key,
                    next_attempt=attempt + 1,
                    wait_s=wait_s,
                    soft_resume=True,
                )
                time.sleep(wait_s)
                self._check_stop()
        raise ResearchPaused(f"{step_key} tamamlanamadı")

    def _parse_truncated_prefix(self, raw: str, *, agent: Agent, step_key: str) -> dict[str, Any]:
        try:
            recovered = parse_truncated_object_prefix(raw)
        except StructuredOutputError as exc:
            self.trace.log(
                "structured_output_truncated_recovery_failed",
                step_key=step_key,
                agent=agent.name,
                error=str(exc),
            )
            raise ResearchPaused(
                f"{agent.name} çıktısı token sınırında kesildi ve güvenli JSON prefix'i kurtarılamadı: {exc}"
            ) from exc
        self.trace.log(
            "structured_output_truncated_prefix_recovered",
            step_key=step_key,
            agent=agent.name,
            recovered_keys=sorted(recovered),
        )
        return recovered

    def _retry_truncated_json(self, agent: Agent, prompt: str, step_key: str) -> dict[str, Any]:
        first_meta = dict(self._llm_step_meta.get(step_key, {}))
        current_effort = first_meta.get("reasoning_effort_sent") or agent.reasoning_effort
        retry_effort = next_lower_supported_effort(agent.model, current_effort)
        retry_agent = replace(agent, reasoning_effort=retry_effort)
        retry_step = f"{step_key}:truncated_retry"
        retry_prompt = prompt + "\n\n" + _RETRY_INSTRUCTION

        retry_raw = self._call(retry_agent, retry_prompt, retry_step)
        retry_meta = dict(self._llm_step_meta.get(retry_step, {}))
        retry_empty = bool(
            str(retry_meta.get("finish_reason") or "").casefold() == "length"
            and not str(retry_raw or "").strip()
        )
        outcome = "failed" if retry_empty else "recovered"
        self.trace.log(
            "truncated_retry",
            step_key=step_key,
            retry_step_key=retry_step,
            agent=agent.name,
            model=agent.model,
            effort_transition={"first": current_effort, "retry": retry_effort},
            first=_retry_event_payload(first_meta),
            retry=_retry_event_payload(retry_meta),
            outcome=outcome,
        )
        if retry_empty:
            raise ResearchPaused(
                f"{agent.name} automatic truncated retry already exhausted; ikinci length+empty yanıtından sonra fail-closed durduruldu."
            )

        self._llm_step_meta[step_key] = {
            **retry_meta,
            "truncation_retry": "recovered",
            "retry_step_key": retry_step,
        }

        try:
            return parse_json_object(retry_raw)
        except StructuredOutputError as first:
            self.trace.log(
                "structured_output_parse_failed",
                step_key=retry_step,
                agent=agent.name,
                truncated=bool(retry_meta.get("truncated")),
                error=str(first),
            )

        if bool(retry_meta.get("truncated")):
            return self._parse_truncated_prefix(retry_raw, agent=agent, step_key=retry_step)

        repair_raw = self._call(retry_agent, repair_instruction(retry_raw), f"{retry_step}:json_repair")
        try:
            repaired = parse_json_object(repair_raw)
        except StructuredOutputError as exc:
            self.trace.log(
                "structured_output_repair_failed",
                step_key=retry_step,
                agent=agent.name,
                error=str(exc),
            )
            raise ResearchPaused(
                f"{agent.name} truncated retry sonrası geçerli JSON üretemedi; araştırma fail-closed olarak durduruldu: {exc}"
            ) from exc
        self.trace.log("structured_output_repaired", step_key=retry_step, agent=agent.name)
        return repaired

    def _call_json(self, agent: Agent, prompt: str, step_key: str) -> dict[str, Any]:
        raw = self._call(agent, prompt, step_key)
        truncated = bool(self._llm_step_meta.get(step_key, {}).get("truncated"))
        try:
            return parse_json_object(raw)
        except StructuredOutputError as first:
            self.trace.log(
                "structured_output_parse_failed",
                step_key=step_key,
                agent=agent.name,
                truncated=truncated,
                error=str(first),
            )

        if truncated:
            if not str(raw or "").strip():
                return self._retry_truncated_json(agent, prompt, step_key)
            return self._parse_truncated_prefix(raw, agent=agent, step_key=step_key)

        repair_raw = self._call(agent, repair_instruction(raw), f"{step_key}:json_repair")
        try:
            repaired = parse_json_object(repair_raw)
        except StructuredOutputError as exc:
            self.trace.log(
                "structured_output_repair_failed",
                step_key=step_key,
                agent=agent.name,
                error=str(exc),
            )
            raise ResearchPaused(
                f"{agent.name} geçerli JSON üretemedi; araştırma fail-closed olarak durduruldu: {exc}"
            ) from exc
        self.trace.log("structured_output_repaired", step_key=step_key, agent=agent.name)
        return repaired

    def _search_literature(self, query: str, limit: int = 8) -> list[Paper]:
        fingerprint = content_fingerprint("literature_search:v3", {"query": query.strip(), "limit": int(limit), "client": type(self.literature).__name__})
        key = f"literature:search:{fingerprint[:20]}"
        cached = self._cache_get(key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            papers = []
            for raw in cached.get("papers", []):
                try:
                    papers.append(Paper(**raw))
                except TypeError:
                    continue
            self._literature_reliable = bool(papers)
            self.trace.log("step_reused", step_key=key, records=len(papers), fingerprint=fingerprint)
            return papers
        self._check_stop()
        self.trace.log("literature_search_start", query=query, limit=limit, fingerprint=fingerprint)
        try:
            papers = self.literature.search(query, limit=limit)
        except LiteratureSearchEmpty as exc:
            self._literature_reliable = False
            self.trace.log("literature_search_inconclusive", query=query, warning=str(exc), fingerprint=fingerprint)
            return []
        except Exception as exc:
            self._literature_reliable = False
            self.trace.log("literature_search_error", query=query, error=str(exc), fingerprint=fingerprint)
            return []
        payload = [paper.as_dict() for paper in papers]
        self._literature_reliable = bool(payload)
        self.trace.log("literature_search", query=query, results=payload, fingerprint=fingerprint)
        self._cache_put(key, {"status": "COMPLETE", "fingerprint": fingerprint, "query": query, "limit": int(limit), "papers": payload, "completed_at": now_iso()})
        return papers

    def _cached_workspace_action(self, cache_key: str, action: dict[str, Any]) -> WorkspaceActionResult:
        fingerprint = content_fingerprint("code_experiment_action:v3", action)
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw_value = cached.get("result")
            raw = dict(raw_value) if isinstance(raw_value, dict) else {}
            metadata = dict(raw.get("metadata") or {})
            action_name = str(action.get("action") or "").strip().lower()
            raw_result = WorkspaceActionResult(
                bool(raw.get("ok")),
                str(raw.get("action") or "unknown"),
                str(raw.get("output") or ""),
                str(raw.get("error") or ""),
                metadata,
            )
            if infrastructure_failure(raw_result):
                self.trace.log(
                    "legacy_infrastructure_cache_invalidated",
                    step_key=cache_key,
                    kind="workspace_action",
                    action=action_name,
                )
                self._cache_delete(cache_key)
            else:
                hash_field = {
                        "run_python": "script_sha256",
                        "read_file": "sha256",
                        "write_file": "sha256",
                        "patch_file": "sha256",
                    }.get(action_name)
                if hash_field is not None:
                    relative_path = str(action.get("path") or "")
                    expected_sha = str(metadata.get(hash_field) or "")
                    current_sha = ""
                    try:
                        target = self.code_workspace._resolve(relative_path, must_exist=True)
                        current_sha = sha256_file(target)
                    except (FileNotFoundError, OSError, ValueError):
                        current_sha = ""
                    if not expected_sha or current_sha != expected_sha:
                        self.trace.log(
                            "cache_sha_miss",
                            step_key=cache_key,
                            action=action_name,
                            path=relative_path,
                            expected_sha256=expected_sha or None,
                            current_sha256=current_sha or None,
                        )
                        self._cache_delete(cache_key)
                    else:
                        self.trace.log("step_reused", step_key=cache_key, tool="code_experiment_action")
                        return raw_result
                else:
                    self.trace.log("step_reused", step_key=cache_key, tool="code_experiment_action")
                    return raw_result
        result = self.code_workspace.execute(action)
        self._cache_put(
            cache_key,
            {
                "status": "INFRA_FAILED" if infrastructure_failure(result) else "COMPLETE",
                "fingerprint": fingerprint,
                "result": result.as_dict(),
                "completed_at": now_iso(),
            },
        )
        return result

    @staticmethod
    def _task_jaccard(left: str, right: str) -> float:
        left_tokens = set(re.findall(r"\w+", str(left or "").casefold()))
        right_tokens = set(re.findall(r"\w+", str(right or "").casefold()))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _repeated_next_task_warning(
        self,
        *,
        current_item_id: str,
        current_task: str,
        target_id: str | None,
    ) -> str:
        previous_items = [
            item
            for item in self.state.list_items(kind="conjecture")
            if item.id != current_item_id and item.status != "DROPPED"
        ]
        if not previous_items:
            return ""
        previous = previous_items[-1]
        metadata = dict(previous.metadata or {})
        if str(metadata.get("manager_decision") or "").upper() != "REVISE":
            return ""
        previous_target = str(metadata.get("target_id") or "").strip()
        if target_id and previous_target and previous_target != target_id:
            return ""
        input_task = str(metadata.get("input_next_task") or "").strip()
        manager_task = str(metadata.get("manager_next_task") or "").strip()
        if not input_task or not manager_task:
            return ""
        first_similarity = self._task_jaccard(input_task, manager_task)
        second_similarity = self._task_jaccard(manager_task, current_task)
        similarity = min(first_similarity, second_similarity)
        if similarity < 0.80:
            return ""
        warning = "bu görev iki turdur tekrarlıyor; ya farklı bir aday iste ya da DROPPED öner"
        self.trace.log(
            "repeated_next_task",
            item_id=current_item_id,
            previous_item_id=previous.id,
            target_id=target_id,
            similarity=similarity,
            previous_input_task=input_task,
            previous_next_task=manager_task,
            current_task=current_task,
        )
        return warning

    def _bind_code_definitions(
        self,
        *,
        proposal: dict[str, Any],
        item_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if str(request.get("tool") or "").strip().lower() != "code_experiment":
            return request
        raw_definitions = proposal.get("definitions")
        definitions = raw_definitions if isinstance(raw_definitions, dict) else {}
        if not definitions:
            return request
        symbols: list[str] = []
        snippets: list[str] = []
        for raw_symbol, raw_source in definitions.items():
            symbol = str(raw_symbol or "").strip()
            source = str(raw_source or "").strip()
            if not symbol or not symbol.isidentifier() or symbol.startswith("__"):
                return {
                    **request,
                    "_definitions_error": f"Geçersiz definitions sembol adı: {symbol!r}",
                }
            if not source:
                return {
                    **request,
                    "_definitions_error": f"definitions[{symbol!r}] boş olamaz.",
                }
            symbols.append(symbol)
            snippets.append(source)
        bundle = "\n\n".join(snippets).strip() + "\n"
        try:
            bound_names = top_level_bound_names(bundle)
        except SyntaxError as exc:
            return {**request, "_definitions_error": f"definitions Python syntax error: {exc}"}
        missing = [symbol for symbol in symbols if symbol not in bound_names]
        if missing:
            return {
                **request,
                "_definitions_error": "definitions anahtarı kendi Python adını bağlamıyor: " + ", ".join(missing),
            }
        written = self.code_workspace.write_file("definitions.py", bundle)
        if not written.ok:
            self.trace.log(
                "definitions_rejected",
                item_id=item_id,
                error=written.error,
                symbols=symbols,
            )
            return {**request, "_definitions_error": str(written.error or "definitions policy rejected")}
        metadata = dict(written.metadata or {})
        definitions_sha256 = str(metadata.get("sha256") or "")
        self.state.update_item(
            item_id,
            metadata={
                "definitions_file": "definitions.py",
                "definitions_sha256": definitions_sha256,
                "definition_symbols": symbols,
            },
        )
        self.trace.log(
            "definitions_written",
            item_id=item_id,
            file="definitions.py",
            definitions_sha256=definitions_sha256,
            symbols=symbols,
        )
        return {
            **request,
            "_definitions_file": "definitions.py",
            "_definitions_sha256": definitions_sha256,
            "_definition_symbols": symbols,
        }

    def _run_code_experiment(self, request: dict[str, Any], step_key: str) -> ToolResult:
        if self.code_agent is None:
            return ToolResult(False, "code_experiment", error="CodeExperimentAgent yapılandırılmamış.", metadata={"evidence_level": "COMPUTATION_ONLY"})
        definitions_error = str(request.get("_definitions_error") or "").strip()
        if definitions_error:
            return ToolResult(
                False,
                "code_experiment",
                error="definitions.py reddedildi: " + definitions_error,
                metadata={
                    "status": "DEFINITIONS_INVALID",
                    "evidence_level": "COMPUTATION_ONLY",
                    "definitions_error": definitions_error,
                },
            )
        task = str(request.get("task") or request.get("goal") or "").strip() or "Aday iddiayı küçük deterministic deneylerle sınamaya çalış."
        symbols_raw = request.get("_definition_symbols")
        symbols = [str(value) for value in symbols_raw] if isinstance(symbols_raw, list) else []
        return self.code_runner.run(
            agent=self.code_agent,
            task=task,
            step_key=step_key,
            call_agent=self._call,
            execute_cached=self._cached_workspace_action,
            source=str(request.get("source") or ""),
            definitions_file=str(request.get("_definitions_file") or ""),
            definition_symbols=symbols,
        )

    def _cached_formal_result(self, raw: dict[str, Any]) -> ToolResult:
        result = ToolResult(
            bool(raw.get("ok")),
            str(raw.get("tool") or "lean"),
            str(raw.get("output") or ""),
            str(raw.get("error") or ""),
            dict(raw.get("metadata") or {}),
        )
        metadata = dict(result.metadata or {})
        if result.ok and metadata.get("formal_verified") is True:
            filename = Path(str(metadata.get("file") or "")).name
            candidate = self.state.root / "formal" / "candidates" / filename
            if not filename or not candidate.is_file() or sha256_file(candidate) != str(metadata.get("lean_sha256") or ""):
                return ToolResult(
                    False,
                    "lean",
                    error="Cached formal evidence rejected: bound Lean file is missing or its SHA-256 changed.",
                    metadata={**metadata, "formal_verified": False},
                )
        return result

    def _formal_tool(self, request: dict[str, Any], step_key: str) -> ToolResult:
        if self._active_iteration is None or not self._active_item_id:
            return ToolResult(False, "lean", error="Formal tool current iteration/item binding olmadan çalışamaz.")

        theorem_name = str(request.get("theorem_name") or "").strip()
        theorem_type = str(request.get("theorem_type") or "").strip()
        source = str(request.get("source") or "")
        filename = f"iter-{self._active_iteration}-{self._active_item_id}.lean"
        enriched = {
            "tool": "lean_draft",
            "file": filename,
            "source": source,
            "theorem_name": theorem_name,
            "theorem_type": theorem_type,
            "item_id": self._active_item_id,
            "iteration": self._active_iteration,
            "claim_hash": self._active_claim_hash,
            "claim_sha256": self._active_claim_sha256,
        }
        fingerprint = content_fingerprint("bound_formal_tool:v2", enriched)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw = cached.get("result")
            if isinstance(raw, dict):
                self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                return self._cached_formal_result(raw)

        self._check_stop()
        self._set_runtime(current_step=step_key)
        self.trace.log("tool_start", request={k: v for k, v in enriched.items() if k != "source"}, step_key=step_key)
        draft = self.toolbox.lean.draft_source(
            filename,
            source,
            theorem_name=theorem_name,
            theorem_type=theorem_type,
            item_id=self._active_item_id,
            iteration=self._active_iteration,
            claim_hash=self._active_claim_hash,
            claim_sha256=self._active_claim_sha256,
        )
        if not draft.ok:
            result = draft
        else:
            dmeta = dict(draft.metadata or {})
            result = self.toolbox.lean.check_file(
                filename,
                expected_sha256=str(dmeta.get("lean_sha256") or ""),
                expected_item_id=self._active_item_id,
                expected_iteration=self._active_iteration,
                expected_claim_hash=self._active_claim_hash,
                expected_claim_sha256=self._active_claim_sha256,
                expected_theorem_name=theorem_name,
                expected_theorem_type=theorem_type,
            )
            merged = dict(dmeta)
            merged.update(result.metadata or {})
            merged["draft_checked_same_step"] = True
            result.metadata = merged
        self.trace.log("tool_result", step_key=step_key, **result.as_dict())
        self._cache_put(
            step_key,
            {"status": "COMPLETE", "fingerprint": fingerprint, "result": result.as_dict(), "completed_at": now_iso()},
        )
        return result

    @staticmethod
    def _normalize_structural_counterexample(result: ToolResult | None) -> ToolResult | None:
        """Keep the legacy tropical mismatch bridge until the reviewed pack wires it.

        ``STRUCTURE_MISMATCH`` is not emitted by a reviewed problem-pack checker
        yet; the Tropical pack will connect this normalization when that formal
        checker exists.
        """

        if result is None or result.tool != "tropical_grid":
            return result
        metadata = dict(result.metadata or {})
        if str(metadata.get("status") or "").upper() != "STRUCTURE_MISMATCH":
            return result
        metadata["counterexample_type"] = "PROVENANCE_STRUCTURE_MISMATCH"
        metadata["raw_status"] = "STRUCTURE_MISMATCH"
        metadata["status"] = "COUNTEREXAMPLE"
        result.metadata = metadata
        return result

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        name = str((request or {}).get("tool") or "none").strip().lower()
        if name == "lean":
            result = ToolResult(
                False,
                "lean",
                error="Direct lean check disabled. Use lean_draft; the engine checks the exact bound draft automatically.",
            )
            self.trace.log("tool_result", step_key=step_key, **result.as_dict())
            return result
        if name == "lean_draft":
            return self._formal_tool(dict(request or {}), step_key)

        fingerprint = content_fingerprint("tool_step:v3", request or {"tool": "none"})
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw = cached.get("result")
            if isinstance(raw, dict):
                cached_result = ToolResult(
                    bool(raw.get("ok")),
                    str(raw.get("tool") or "unknown"),
                    str(raw.get("output") or ""),
                    str(raw.get("error") or ""),
                    dict(raw.get("metadata") or {}),
                )
                stdout_sha = str((cached_result.metadata or {}).get("stdout_sha256") or "")
                stdout_hash_mismatch = bool(
                    cached_result.tool == "code_experiment"
                    and cached_result.ok
                    and stdout_sha
                    and hashlib.sha256(cached_result.output.encode("utf-8")).hexdigest() != stdout_sha
                )
                if stdout_hash_mismatch:
                    self.trace.log(
                        "code_experiment_stdout_cache_invalidated",
                        step_key=step_key,
                        expected_stdout_sha256=stdout_sha,
                    )
                    self._cache_delete(step_key)
                elif infrastructure_failure(cached_result):
                    self.trace.log(
                        "legacy_infrastructure_cache_invalidated",
                        step_key=step_key,
                        kind="tool",
                        tool=cached_result.tool,
                    )
                    self._cache_delete(step_key)
                else:
                    self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                    return self._normalize_structural_counterexample(cached_result)
            else:
                return None
        self._check_stop()
        self._set_runtime(current_step=step_key)
        if name not in {"", "none"}:
            self.trace.log("tool_start", request=request, step_key=step_key)
        tool_result: ToolResult | None = (
            self._run_code_experiment(request or {}, step_key)
            if name == "code_experiment"
            else self.registry.execute(request)
        )
        tool_result = self._normalize_structural_counterexample(tool_result)
        if tool_result:
            self.trace.log("tool_result", step_key=step_key, **tool_result.as_dict())
        self._cache_put(
            step_key,
            {
                "status": (
                    "INFRA_FAILED"
                    if tool_result is not None and infrastructure_failure(tool_result)
                    else "COMPLETE"
                ),
                "fingerprint": fingerprint,
                "result": tool_result.as_dict() if tool_result else None,
                "completed_at": now_iso(),
            },
        )
        return tool_result

    def _iteration_item(self, iteration: int):
        candidates = [
            item
            for item in self.state.list_items(kind="conjecture")
            if int(item.metadata.get("iteration", -1)) == iteration and item.status != "DROPPED"
        ]
        return candidates[-1] if candidates else None

    def _iteration_snapshot(self, iteration: int, next_task: str) -> dict[str, Any]:
        existing = self.step_store.get_iteration_snapshot(iteration)
        if existing:
            return existing
        context = self.state.research_context()
        revision = self.state.revision()
        self.step_store.put_iteration_snapshot(iteration, ledger_revision=revision, ledger_context=context, payload={"next_task": next_task})
        self.trace.log("iteration_snapshot_frozen", iteration=iteration, ledger_revision=revision, ledger_context_chars=len(context))
        return self.step_store.get_iteration_snapshot(iteration) or {}

    def _proposal_for_iteration(
        self,
        iteration: int,
        snapshot: dict[str, Any],
        proposer: Agent,
        prompt: str,
        step_key: str,
    ) -> tuple[dict[str, Any], Any | None]:
        item = self._iteration_item(iteration)
        if item is None:
            return self._call_json(proposer, prompt, step_key), None

        proposal = snapshot.get("proposal")
        snapshot_hash = str(snapshot.get("proposal_hash") or "")
        item_hash = str(item.metadata.get("proposal_hash") or "")
        computed_hash = content_fingerprint("proposal:v1", proposal) if isinstance(proposal, dict) else ""
        if (
            not isinstance(proposal, dict)
            or not snapshot_hash
            or not item_hash
            or snapshot_hash != item_hash
            or computed_hash != item_hash
        ):
            raise ResearchPaused(
                f"Bu koşu tur {iteration} ortasında durmuş ve öneri kaydı eksik veya ledger ile uyuşmuyor. "
                "Farklı bir iddiaya kanıt bağlanmayacak. "
                f'Research Control\'daki "Tur {iteration}\'i yeniden başlat" seçeneğini kullan ya da yeni proje aç.'
            )
        self.trace.log(
            "proposal_reused_from_ledger",
            iteration=iteration,
            item_id=item.id,
            reason="iteration item already bound",
            proposal_hash=item_hash,
        )
        return dict(proposal), item

    def _ensure_item_matches_proposal(self, iteration: int, proposal: dict[str, Any], snapshot: dict[str, Any]):
        claim = str(proposal.get("claim") or "Boş iddia")
        title = str(proposal.get("title") or f"Iteration {iteration} candidate")
        proposal_hash = content_fingerprint("proposal:v1", proposal)
        item = self._iteration_item(iteration)
        if item is None:
            item = self.state.add_item(
                "conjecture",
                title=title,
                claim=claim,
                metadata={
                    "iteration": iteration,
                    "proposal": proposal,
                    "proposal_hash": proposal_hash,
                    "ledger_revision": snapshot.get("ledger_revision"),
                },
            )
            self.trace.log("state_change", action="create", item_id=item.id, kind="conjecture", old_status=None, new_status="OPEN", title=title, claim=claim)
        else:
            old_hash = str(item.metadata.get("proposal_hash") or "")
            if old_hash and old_hash != proposal_hash:
                raise ResearchPaused(
                    f"Iteration {iteration} integrity mismatch: ledger item {item.id} was created from a different proposal. Evidence will not be attached to a mismatched claim."
                )
            if not old_hash and item.claim.strip() != claim.strip():
                raise ResearchPaused(
                    f"Iteration {iteration} legacy integrity mismatch: cached proposal claim differs from ledger item {item.id}."
                )
        self.step_store.update_iteration_payload(iteration, proposal=proposal, proposal_hash=proposal_hash, item_id=item.id)
        self._active_iteration = int(iteration)
        self._active_item_id = item.id
        self._active_claim_hash = content_fingerprint("claim:v1", item.claim)
        self._active_claim_sha256 = hashlib.sha256(item.claim.encode("utf-8")).hexdigest()
        return item

    def _validate_proposal_target(
        self,
        proposer: Agent,
        proposal: dict[str, Any],
        *,
        contract: ResearchContract | None,
        selectable_ids: list[str],
        iteration: int,
    ) -> tuple[dict[str, Any], str | None]:
        if contract is None:
            return proposal, None

        def valid_target(value: dict[str, Any]) -> str | None:
            target_id = str(value.get("target_id") or "").strip()
            if target_id not in selectable_ids:
                return None
            try:
                contract.target(target_id, require_open=True)
            except (KeyError, ValueError):
                return None
            return target_id

        target_id = valid_target(proposal)
        if target_id:
            return proposal, target_id
        self.trace.log(
            "proposal_target_rejected",
            iteration=iteration,
            selected=str(proposal.get("target_id") or ""),
            valid_target_ids=selectable_ids,
        )
        repair = (
            "The previous proposal violated the frozen research target protocol. "
            f"Choose exactly one target_id from {json.dumps(selectable_ids, ensure_ascii=False)} and return the COMPLETE corrected proposal JSON. "
            "Do not invent claim_role; code assigns it. Previous proposal:\n"
            + json.dumps(proposal, ensure_ascii=False)
        )
        repaired = self._call_json(proposer, repair, f"iter:{iteration}:target_repair")
        target_id = valid_target(repaired)
        if not target_id:
            self.trace.log(
                "proposal_target_rejected",
                iteration=iteration,
                selected=str(repaired.get("target_id") or ""),
                valid_target_ids=selectable_ids,
                after_repair=True,
            )
            raise ResearchPaused("Theorist geçerli bir open target seçemedi")
        return repaired, target_id

    def _incomplete_target(
        self,
        proposal: dict[str, Any],
        *,
        contract: ResearchContract | None,
        selectable_ids: list[str],
        iteration: int,
    ) -> str | None:
        if contract is None:
            return None
        target_id = str(proposal.get("target_id") or "").strip()
        if target_id in selectable_ids:
            try:
                contract.target(target_id, require_open=True)
            except (KeyError, ValueError):
                pass
            else:
                return target_id
        self.trace.log(
            "proposal_target_rejected",
            iteration=iteration,
            selected=target_id,
            valid_target_ids=selectable_ids,
            incomplete_output=True,
            repair_skipped=True,
        )
        return None

    def _pilot_gate(
        self,
        contract: ResearchContract | None,
        *,
        allow_discovery_without_pilot: bool,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str], bool]:
        if contract is None:
            return {}, [], False
        if not contract.frozen:
            self.controller.set_research_phase("FORMALIZATION")
            raise ResearchPaused("Research contract dondurulmadan discovery başlatılamaz.")
        grouped = pilot_evidence_by_target(contract, self.state)
        overridden = False
        if contract.pilot_policy == "REQUIRED" and not grouped:
            if allow_discovery_without_pilot:
                overridden = True
                self.trace.log("pilot_gate_overridden", contract_hash=contract.contract_hash)
            else:
                self.controller.set_research_phase("PILOT")
                self.trace.log("pilot_missing", contract_hash=contract.contract_hash, pilot_policy=contract.pilot_policy)
                raise ResearchPaused("Pilot evidence yok; deterministic pilot çalıştırılmadan LLM discovery başlatılmaz.")
        elif contract.pilot_policy == "OPTIONAL" and not grouped:
            self.trace.log("pilot_missing", contract_hash=contract.contract_hash, pilot_policy=contract.pilot_policy)
        selectable = selectable_target_ids(
            contract,
            grouped,
            allow_discovery_without_pilot=overridden,
        )
        if not selectable:
            if not contract.open_target_ids():
                return grouped, [], overridden
            self.controller.set_research_phase("PILOT")
            raise ResearchPaused("Discovery için seçilebilir OPEN hedef yok.")
        return grouped, selectable, overridden

    def run_pilot(
        self,
        *,
        target_id: str,
        script_name: str,
        args: list[str] | None = None,
    ):
        """Run one deterministic checked-in pilot without entering the LLM loop."""

        with self.controller.lock:
            contract = ResearchContract.load(self.state.root)
            if not contract.frozen:
                raise ResearchPaused("Pilot run için frozen research contract gerekli.")
            contract.target(target_id, require_open=True)
            self.controller.set_research_phase("PILOT")
            request = {"tool": "script", "name": script_name, "args": list(args or [])}
            result = self._tool(request, f"pilot:{target_id}:{content_fingerprint('pilot:v1', request)[:16]}")
            if result is None:
                raise ResearchPaused("Deterministic pilot tool result üretmedi.")
            evidence = evidence_from_tool_result(result, request=request, contract=contract, target_id=target_id)
            evidence = validate_evidence_binding(evidence, contract=contract)
            item = self.state.add_item(
                "experiment",
                title=f"Deterministic pilot for {target_id}",
                claim=f"Pilot script {script_name}",
                status="KNOWN",
                metadata={
                    "target_id": target_id,
                    "contract_hash": contract.contract_hash,
                    "evidence": evidence.as_dict(),
                    "pilot": True,
                },
            )
            self.trace.log(
                "pilot_result",
                item_id=item.id,
                target_id=target_id,
                evidence=evidence.as_dict(),
            )
            transition = contract.evaluate_target_transition(target_id, ledger_records(self.state))
            if transition is not None:
                updated = contract.apply_target_transition(target_id, transition)
                contract.save(self.state.root)
                self.trace.log(
                    "pilot_target_transition_applied",
                    item_id=item.id,
                    target_id=target_id,
                    status=updated.status,
                    closed_by=list(updated.closed_by),
                    reason=transition.reason,
                )
            return item

    def run(
        self,
        problem: str,
        *,
        manager: Agent,
        proposer: Agent,
        critic: Agent,
        verifier: Agent,
        auditor: Agent,
        literature_agent: Agent | None = None,
        code_agent: Agent | None = None,
        iterations: int = 5,
        literature_query: str | None = None,
        checkpoint_every: int = 2,
        allow_discovery_without_pilot: bool = False,
    ) -> str:
        try:
            with self.controller.lock:
                self.trace.log("project_lock_acquired", project_root=str(self.state.root), pid=os.getpid())
                try:
                    restart_marker = consume_iteration_restart(self.state.root)
                    if restart_marker is not None:
                        self.trace.log("iteration_restarted", **restart_marker)
                    if code_agent is None:
                        configured_code_effort = self.code_settings.get("reasoning_effort")
                        if configured_code_effort in (None, ""):
                            configured_code_effort = get_reasoning_effort("CodeExperimentAgent")
                        if configured_code_effort is None:
                            configured_code_effort = "low"
                        code_agent = Agent(
                            name="CodeExperimentAgent",
                            system_prompt=CODE_EXPERIMENT_SYSTEM_PROMPT,
                            model=str(self.code_settings.get("model") or os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or proposer.model),
                            temperature=0.2,
                            max_tokens=proposer.max_tokens,
                            reasoning_effort=str(configured_code_effort) if configured_code_effort else None,
                        )
                    elif not code_agent.system_prompt.strip():
                        code_agent.system_prompt = CODE_EXPERIMENT_SYSTEM_PROMPT
                    self.code_agent = code_agent
                    self.code_settings["reasoning_effort"] = code_agent.reasoning_effort
                    agents = {
                        "ResearchManager": manager,
                        "Theorist": proposer,
                        "AdversarialCritic": critic,
                        "VerificationEngineer": verifier,
                        "IndependentAuditor": auditor,
                    }
                    if literature_agent is not None:
                        agents["LiteratureScout"] = literature_agent
                    agents["CodeExperimentAgent"] = code_agent
                    self._save_config(
                        problem,
                        iterations,
                        literature_query,
                        checkpoint_every,
                        agents,
                        allow_discovery_without_pilot=allow_discovery_without_pilot,
                    )
                    self.controller.clear_stale_stop()
                    result = self._run_inner(
                        problem,
                        manager=manager,
                        proposer=proposer,
                        critic=critic,
                        verifier=verifier,
                        auditor=auditor,
                        literature_agent=literature_agent,
                        iterations=iterations,
                        literature_query=literature_query,
                        checkpoint_every=checkpoint_every,
                        allow_discovery_without_pilot=allow_discovery_without_pilot,
                    )
                    self._set_runtime(status="COMPLETED", last_error="")
                    return result
                except ResearchStopped as exc:
                    self._set_runtime(status="STOPPED", last_error=str(exc))
                    self.trace.log("run_stopped", error=str(exc))
                    return "# Araştırma durduruldu\n\nKalıcı state, iteration snapshot ve tamamlanan adımlar korundu. Devam edildiğinde ilk tamamlanmamış adımdan ilerlenir."
                except ResearchPaused as exc:
                    self._set_runtime(status="PAUSED_ERROR", last_error=str(exc))
                    self.trace.log("run_paused", error=str(exc))
                    return f"# Araştırma hata nedeniyle beklemeye alındı\n\n{exc}\n\nBelirsiz/bozuk structured output veya integrity uyuşmazlığı sessizce geçilmedi."
                except Exception as exc:
                    self._set_runtime(status="PAUSED_ERROR", last_error=repr(exc))
                    self.trace.log("run_unhandled_error", error=repr(exc))
                    raise
                finally:
                    self.trace.log("project_lock_releasing", project_root=str(self.state.root))
        finally:
            self._active_iteration = None
            self._active_item_id = ""
            self._active_claim_hash = ""
            self._active_claim_sha256 = ""

    def _run_inner(
        self,
        problem: str,
        *,
        manager: Agent,
        proposer: Agent,
        critic: Agent,
        verifier: Agent,
        auditor: Agent,
        literature_agent: Agent | None,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        allow_discovery_without_pilot: bool,
    ) -> str:
        try:
            self.state.freeze_problem(problem)
        except ValueError as exc:
            raise ResearchPaused(f"Research contract integrity error: {exc}") from exc
        self.trace.log("problem_frozen", problem=problem)

        try:
            contract = ResearchContract.load_optional(self.state.root)
        except ValueError as exc:
            raise ResearchPaused(f"Research contract integrity error: {exc}") from exc
        pilot_groups, selectable_ids, gate_overridden = self._pilot_gate(
            contract,
            allow_discovery_without_pilot=allow_discovery_without_pilot,
        )
        contract_block = contract.prompt_block(target_ids=selectable_ids) if contract is not None else ""
        pilot_block = pilot_prompt_block(contract, pilot_groups) if contract is not None else ""

        runtime = self._runtime()
        completed = int(runtime.get("completed_iterations", 0) or 0)
        next_task = str(runtime.get("next_task") or "").strip() or "Problemi daralt; bilinen sınırları ihlal etmeyen, çürütülebilir tek bir lemma, construction veya lower-bound mekanizması öner."
        self._set_runtime(status="RUNNING", last_error="")

        if contract is not None and not selectable_ids and not contract.open_target_ids():
            self.controller.set_research_phase("PUBLICATION")
            self.trace.log(
                "run_completed_all_targets_closed",
                completed_iterations=completed,
                requested_iterations=int(iterations),
                source="pre_iteration_gate",
            )
            final_path = self.state.checkpoint("final", note="All frozen targets already resolved by machine evidence.")
            return "# Teorem Araştırması Sonucu\n\nTüm frozen hedefler makine kanıtıyla zaten çözüldü.\n\nCheckpoint: `" + str(final_path) + "`"

        papers = self._search_literature(literature_query or problem)
        literature_context = paper_context(papers)
        if literature_agent is not None:
            lprompt = literature_prompt(problem, literature_context, reliable=self._literature_reliable)
            literature_report = self._call(literature_agent, lprompt, "literature:agent")
            title = "Literature screening report"
            existing = [x for x in self.state.list_items(kind="known_result") if x.title == title]
            if not existing:
                known = self.state.add_item(
                    "known_result",
                    title,
                    literature_report,
                    status="KNOWN",
                    metadata={"screening_only": True, "retrieval_reliable": self._literature_reliable},
                )
                self.trace.log("state_change", action="create", item_id=known.id, kind="known_result", status="KNOWN", title=title)
            reliability_label = "retrieval had candidate records" if self._literature_reliable else "retrieval inconclusive - NOT a novelty signal"
            literature_context += f"\n\nLLM LITERATURE SCREEN ({reliability_label}):\n{literature_report}"

        outcomes: list[IterationOutcome] = []
        for iteration in range(completed + 1, int(iterations) + 1):
            if contract is not None and not selectable_ids:
                self.controller.set_research_phase("PUBLICATION")
                self.trace.log(
                    "run_completed_all_targets_closed",
                    completed_iterations=int(self._runtime().get("completed_iterations", 0) or 0),
                    requested_iterations=int(iterations),
                    source="iteration_gate",
                )
                break
            self._check_stop()
            self._set_runtime(current_iteration=iteration, current_step="iteration_start")
            snapshot = self._iteration_snapshot(iteration, next_task)
            frozen_next_task = str(snapshot.get("next_task") or next_task)
            ledger_context = str(snapshot.get("ledger_context") or "")
            self.trace.log("iteration_start", iteration=iteration, next_task=frozen_next_task, ledger_revision=snapshot.get("ledger_revision"))

            if contract is not None:
                self.controller.set_research_phase("DISCOVERY")
            proposer_step = f"iter:{iteration}:proposer"
            current_proposal_prompt = proposal_prompt(
                problem,
                literature_context,
                ledger_context,
                frozen_next_task,
                self.registry,
                contract_block=contract_block,
                pilot_block=pilot_block,
            )
            proposal, bound_item = self._proposal_for_iteration(
                iteration,
                snapshot,
                proposer,
                current_proposal_prompt,
                proposer_step,
            )
            proposal_incomplete = bool(
                (
                    bound_item is not None
                    and (
                        bound_item.metadata.get("truncated")
                        or bound_item.metadata.get("completion") == "INCOMPLETE_OUTPUT"
                    )
                )
                or (
                    bound_item is None
                    and self._llm_step_meta.get(proposer_step, {}).get("truncated")
                )
            )
            if proposal_incomplete and (
                not str(proposal.get("title") or "").strip()
                or not str(proposal.get("claim") or "").strip()
            ):
                raise ResearchPaused(
                    "Kesilmiş Theorist çıktısında tamamlanmış title ve claim yok; yük taşıyan candidate oluşturulmadı."
                )
            if bound_item is not None:
                if proposal_incomplete:
                    target_id = self._incomplete_target(
                        proposal,
                        contract=contract,
                        selectable_ids=selectable_ids,
                        iteration=iteration,
                    )
                elif contract is None:
                    target_id = None
                else:
                    target_id = str(
                        bound_item.metadata.get("target_id") or proposal.get("target_id") or ""
                    ).strip() or None
                    if target_id is None or target_id not in selectable_ids:
                        raise ResearchPaused(
                            f"Tur {iteration} için ledger'a bağlanmış önerinin target_id değeri artık seçilebilir frozen hedeflerle uyuşmuyor. "
                            "Kanonik öneri sessizce değiştirilmeyecek."
                        )
                    contract.target(target_id, require_open=True)
            elif proposal_incomplete:
                target_id = self._incomplete_target(
                    proposal,
                    contract=contract,
                    selectable_ids=selectable_ids,
                    iteration=iteration,
                )
            else:
                proposal, target_id = self._validate_proposal_target(
                    proposer,
                    proposal,
                    contract=contract,
                    selectable_ids=selectable_ids,
                    iteration=iteration,
                )
            item = self._ensure_item_matches_proposal(iteration, proposal, snapshot)
            if proposal_incomplete:
                self.state.update_item(
                    item.id,
                    status="OPEN",
                    metadata={"truncated": True, "completion": "INCOMPLETE_OUTPUT"},
                )
            claim = item.claim
            if contract is not None and target_id is not None:
                target = contract.target(target_id, require_open=True)
                role = contract.claim_role(target_id, claim)
                protocol_metadata: dict[str, Any] = {
                    "target_id": target_id,
                    "target_hash": target.target_hash,
                    "claim_role": role,
                }
                if contract.pilot_policy == "OPTIONAL" and not pilot_groups.get(target_id):
                    protocol_metadata["pilot_missing"] = True
                if gate_overridden:
                    protocol_metadata["pilot_gate_overridden"] = True
                self.state.update_item(item.id, metadata=protocol_metadata)
                self.trace.log(
                    "claim_role_assigned",
                    iteration=iteration,
                    item_id=item.id,
                    target_id=target_id,
                    claim_role=role,
                )

            request = proposal.get("tool_request")
            tool_request = request if isinstance(request, dict) and not proposal_incomplete else None
            if tool_request is not None:
                tool_request = self._bind_code_definitions(
                    proposal=proposal,
                    item_id=item.id,
                    request=dict(tool_request),
                )
            tool_result = None if proposal_incomplete else self._tool(tool_request, f"iter:{iteration}:tool")
            bound_evidence = None
            if tool_result is not None:
                try:
                    bound_evidence = evidence_from_tool_result(
                        tool_result,
                        request=tool_request,
                        contract=contract,
                        target_id=target_id,
                    )
                    bound_evidence = replace(
                        bound_evidence,
                        metadata={**bound_evidence.metadata, "item_id": item.id},
                    )
                    bound_evidence = validate_evidence_binding(bound_evidence, contract=contract)
                except (KeyError, ValueError) as exc:
                    self.trace.log(
                        "evidence_binding_rejected",
                        iteration=iteration,
                        item_id=item.id,
                        target_id=target_id,
                        error=str(exc),
                    )
                if bound_evidence is not None:
                    self.trace.log(
                        "tool_result_evidence",
                        iteration=iteration,
                        item_id=item.id,
                        evidence_kind=bound_evidence.kind,
                        evidence=bound_evidence.as_dict(),
                    )

            verification = self._call_json(
                verifier,
                verifier_prompt(problem, item.id, proposal, tool_result.as_dict() if tool_result else None, self.registry),
                f"iter:{iteration}:verifier",
            )
            critique = self._call_json(
                critic,
                critic_prompt(problem, item.id, claim, proposal, tool_result.as_dict() if tool_result else None, verification, ledger_context),
                f"iter:{iteration}:critic",
            )
            repeat_warning = self._repeated_next_task_warning(
                current_item_id=item.id,
                current_task=frozen_next_task,
                target_id=target_id,
            )
            manager_decision = self._call_json(
                manager,
                manager_prompt(
                    problem,
                    item.id,
                    claim,
                    tool_result.as_dict() if tool_result else None,
                    verification,
                    critique,
                    contract_block=contract.prompt_block() if contract is not None else "",
                    registry=self.registry,
                    candidate_incomplete=proposal_incomplete,
                    repeat_warning=repeat_warning,
                ),
                f"iter:{iteration}:manager",
            )
            decision = str(manager_decision.get("decision") or "REVISE").upper()
            requested_status = str(manager_decision.get("status") or "OPEN").upper()
            if tool_result and tool_result.tool == "lean" and tool_result.ok and (tool_result.metadata or {}).get("formal_verified"):
                requested_status = "PROVEN"
            expected_claim_hash = content_fingerprint("claim:v1", item.claim)
            if proposal_incomplete:
                status = "OPEN"
                status_reason = "INCOMPLETE_OUTPUT cannot be promoted; provider ended the proposer response at a token limit."
                status_metadata: dict[str, Any] = {
                    "truncated": True,
                    "completion": "INCOMPLETE_OUTPUT",
                    "requested_status_ignored": requested_status,
                }
                self.trace.log(
                    "incomplete_output_not_promotable",
                    iteration=iteration,
                    item_id=item.id,
                    requested=requested_status,
                    granted="OPEN",
                    finish_reason=self._llm_step_meta.get(proposer_step, {}).get("finish_reason"),
                )
            else:
                guard = choose_status(
                    requested_status,
                    tool_result=tool_result,
                    verifier=verification,
                    critic=critique,
                    expected_item_id=item.id,
                    expected_iteration=iteration,
                    expected_claim_hash=expected_claim_hash,
                    evidence=bound_evidence,
                    contract=contract,
                )
                status = guard.granted
                status_reason = guard.reason
                status_metadata = guard.metadata
                if status in {"PROOF_CANDIDATE", "PROVEN"}:
                    self.controller.set_research_phase("PROOF")
                if guard.downgraded:
                    self.trace.log(
                        "status_downgraded_by_guard",
                        iteration=iteration,
                        item_id=item.id,
                        requested=requested_status,
                        granted=status,
                        reason=guard.reason,
                        guard=guard.metadata,
                    )

            counterexample = str(verification.get("counterexample") or critique.get("counterexample") or "").strip()
            deterministic_tool_counterexample = bool(
                bound_evidence
                and bound_evidence.kind == "DETERMINISTIC_COUNTEREXAMPLE"
                and bound_evidence.witness is not None
            )
            if status == "FAIL" and (deterministic_tool_counterexample or counterexample):
                existing = [x for x in self.state.list_items(kind="counterexample") if x.metadata.get("target_id") == item.id]
                if existing:
                    counter = existing[-1]
                else:
                    desc = "Deterministically verified tool counterexample" if deterministic_tool_counterexample else counterexample
                    payload = bound_evidence.witness if deterministic_tool_counterexample and bound_evidence else None
                    counter = self.state.add_counterexample(item.id, desc, payload=payload)
                    self.trace.log("state_change", action="counterexample", item_id=counter.id, target_id=item.id, kind="counterexample", status="KNOWN", detail=desc)
                if bound_evidence is not None:
                    evidence_metadata = bound_evidence.as_dict()
                    self.state.update_item(counter.id, metadata={"evidence": evidence_metadata})
                    self.state.update_item(item.id, metadata={"evidence": evidence_metadata})
            else:
                evidence = [
                    "Verifier: " + json.dumps(verification, ensure_ascii=False),
                    "Critic: " + json.dumps(critique, ensure_ascii=False),
                    "Manager: " + json.dumps(manager_decision, ensure_ascii=False),
                    "StatusGuard: " + status_reason,
                ]
                if tool_result:
                    evidence.append("Tool: " + json.dumps(tool_result.as_dict(), ensure_ascii=False))
                metadata: dict[str, Any] = {
                    "status_guard": status_metadata,
                    "proposal_hash": item.metadata.get("proposal_hash"),
                }
                if proposal_incomplete:
                    metadata.update({"truncated": True, "completion": "INCOMPLETE_OUTPUT"})
                if bound_evidence is not None:
                    metadata["evidence"] = bound_evidence.as_dict()
                if status == "PROVEN" and tool_result and tool_result.tool == "lean":
                    formal_metadata = dict(tool_result.metadata or {})
                    formal_metadata["formal_verified"] = True
                    formal_metadata["lean_file"] = str(formal_metadata.get("file") or "")
                    metadata.update(formal_metadata)
                self.state.update_item(item.id, status=status, evidence=evidence, metadata=metadata)

            if contract is not None:
                target_proposal = manager_decision.get("target_proposal")
                if isinstance(target_proposal, dict) and any(str(value or "").strip() for value in target_proposal.values()):
                    self.trace.log(
                        "target_transition_proposed",
                        iteration=iteration,
                        item_id=item.id,
                        proposal=target_proposal,
                    )
                    if proposal_incomplete:
                        self.trace.log(
                            "target_transition_rejected",
                            iteration=iteration,
                            item_id=item.id,
                            reason="INCOMPLETE_OUTPUT cannot drive a target transition",
                            detail=target_proposal,
                        )
                    else:
                        applied, transition_reason, transition_detail = evaluate_manager_target_proposal(
                            contract,
                            self.state,
                            manager_decision,
                        )
                        self.trace.log(
                            "target_transition_applied" if applied else "target_transition_rejected",
                            iteration=iteration,
                            item_id=item.id,
                            reason=transition_reason,
                            detail=transition_detail,
                        )
                        if applied:
                            contract = ResearchContract.load(self.state.root)
                            pilot_groups = pilot_evidence_by_target(contract, self.state)
                            selectable_ids = selectable_target_ids(
                                contract,
                                pilot_groups,
                                allow_discovery_without_pilot=gate_overridden,
                            )
                            contract_block = contract.prompt_block(target_ids=selectable_ids)
                            pilot_block = pilot_prompt_block(contract, pilot_groups)

            old_status = item.status
            self.trace.log("state_change", action="status", item_id=item.id, kind="conjecture", old_status=old_status, new_status=status, decision=decision, reason=str(manager_decision.get("reason") or ""))
            next_task = str(manager_decision.get("next_task") or frozen_next_task)
            self.state.update_item(
                item.id,
                metadata={
                    "input_next_task": frozen_next_task,
                    "manager_decision": decision,
                    "manager_next_task": next_task,
                },
            )
            outcomes.append(IterationOutcome(item.id, decision, status, next_task))
            self.trace.log("iteration_end", iteration=iteration, item_id=item.id, decision=decision, status=status, next_task=next_task)
            self._set_runtime(completed_iterations=iteration, current_iteration=iteration, current_step="iteration_complete", next_task=next_task, status="RUNNING")

            if checkpoint_every and iteration % checkpoint_every == 0:
                ledger = self.state.research_context(recent_limit=50)
                audit = self._call(
                    auditor,
                    checkpoint_prompt(
                        problem,
                        ledger,
                        iteration,
                        contract_block=contract.prompt_block() if contract is not None else "",
                    ),
                    f"iter:{iteration}:checkpoint_audit",
                )
                title = f"Checkpoint audit {iteration}"
                if not [x for x in self.state.list_items(kind="audit") if x.title == title]:
                    audit_item = self.state.add_item("audit", title, audit, status="KNOWN", metadata={"iteration": iteration, "independent": True})
                    checkpoint_path = self.state.checkpoint(f"iteration-{iteration}", note=audit[:1000])
                    self.trace.log("checkpoint", iteration=iteration, audit_item_id=audit_item.id, path=str(checkpoint_path), audit=audit)

        final_ledger = self.state.research_context(recent_limit=80)
        final_audit = self._call(
            auditor,
            checkpoint_prompt(
                problem,
                final_ledger,
                int(iterations),
                final=True,
                contract_block=contract.prompt_block() if contract is not None else "",
            ),
            "final:audit",
        )
        final_path = self.state.checkpoint("final", note=final_audit[:1500])
        self.trace.log("checkpoint", final=True, path=str(final_path), audit=final_audit)
        lines = ["# Teorem Araştırması Sonucu", "", "## Tur Sonuçları"]
        for outcome in outcomes:
            lines.append(f"- `{outcome.item_id}` — **{outcome.status}** — {outcome.decision} — next: {outcome.next_task}")
        if not outcomes:
            lines.append("- Yeni tur çalıştırılmadı; mevcut state zaten istenen iterasyona kadar tamamlanmıştı.")
        lines += ["", "## Final Bağımsız Audit", final_audit, "", f"Checkpoint: `{final_path}`"]
        return "\n".join(lines)
