from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

from lab.agent import Agent
from lab.client import next_lower_supported_effort
from lab.json_io import StructuredOutputError, parse_json_object, parse_truncated_object_prefix, repair_instruction
from lab.run_controller import ResearchPaused, ResearchStopped, now_iso, retryable
from lab.theorem_engine import TheoremResearchLab as _BaseTheoremResearchLab


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
    )
    return {key: meta.get(key) for key in keys}


class TheoremResearchLab(_BaseTheoremResearchLab):
    """Production theorem workflow with persistent length+empty JSON recovery."""

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

        # PR-C legacy compatibility: the PR-E prompt itself changes the fingerprint.
        # Replaying the old 703-second empty attempt would be pure cost duplication,
        # so the same-model legacy failure is historical state, not a candidate.
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
            effort_transition={
                "first": current_effort,
                "retry": retry_effort,
            },
            first=_retry_event_payload(first_meta),
            retry=_retry_event_payload(retry_meta),
            outcome=outcome,
        )
        if retry_empty:
            raise ResearchPaused(
                f"{agent.name} automatic truncated retry already exhausted; ikinci length+empty yanıtından sonra fail-closed durduruldu."
            )

        # The logical proposal step now reflects the retry result. This is what
        # _run_inner uses to decide whether a parsed proposal is INCOMPLETE_OUTPUT.
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

        repair_raw = self._call(
            retry_agent,
            repair_instruction(retry_raw),
            f"{retry_step}:json_repair",
        )
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
