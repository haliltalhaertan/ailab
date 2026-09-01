from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from lab.agent import Agent
from lab.code_experiment_theorem_lab import TheoremResearchLab as CodeExperimentTheoremResearchLab
from lab.integrity import ProjectRunLock, content_fingerprint
from lab.literature import Paper
from lab.resumable_theorem_lab import (
    ResearchPaused,
    ResearchStopped,
    _atomic_json,
    _now,
    _retryable,
)
from lab.tools import ToolResult


class TheoremResearchLab(CodeExperimentTheoremResearchLab):
    """Final hardened theorem workflow.

    Adds project-level cross-process exclusion, content-addressed cache validation,
    query-aware literature caching, durable structured reasoning continuation, and
    exact run-config persistence on top of the resumable code-experiment lab.
    """

    PARTIAL_RESUME_CHAR_LIMIT = 60_000
    PARTIAL_FLUSH_INTERVAL_S = 0.25
    PARTIAL_FLUSH_CHARS = 1024

    def _cache_delete(self, key: str) -> None:
        cache = self._cache()
        if key in cache:
            cache.pop(key, None)
            _atomic_json(self.cache_path, cache)

    def _llm_fingerprint(self, agent: Agent, prompt: str) -> str:
        return content_fingerprint(
            "llm_step:v2",
            {
                "agent": agent.name,
                "model": agent.model,
                "system_prompt": agent.system_prompt,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
                "reasoning_effort": agent.reasoning_effort,
                "prompt": prompt,
            },
        )

    def _tool_fingerprint(self, request: dict[str, Any] | None) -> str:
        return content_fingerprint("deterministic_tool:v2", request or {"tool": "none"})

    def _save_config(
        self,
        problem: str,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        agents: dict[str, Agent],
    ) -> None:
        augmented = dict(agents)
        if getattr(self, "code_agent", None) is not None:
            augmented["CodeExperimentAgent"] = self.code_agent
        payload = {
            "config_version": 2,
            "problem": problem,
            "iterations": int(iterations),
            "literature_query": literature_query,
            "checkpoint_every": int(checkpoint_every),
            "agents": {
                role: {
                    "name": agent.name,
                    "system_prompt": agent.system_prompt,
                    "model": agent.model,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                    "reasoning_effort": agent.reasoning_effort,
                }
                for role, agent in augmented.items()
            },
            "code_experiment": dict(getattr(self, "code_settings", {}) or {}),
            "saved_at": _now(),
        }
        _atomic_json(self.config_path, payload)

    def run(self, problem: str, **kwargs) -> str:
        lock = ProjectRunLock(self.state.root)
        with lock:
            self.trace.log(
                "project_lock_acquired",
                project_root=str(self.state.root),
                pid=__import__("os").getpid(),
            )
            try:
                return super().run(problem, **kwargs)
            finally:
                self.trace.log("project_lock_releasing", project_root=str(self.state.root))

    @staticmethod
    def _tail(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return "[... önceki kısım context limiti için kırpıldı ...]\n" + text[-limit:]

    def _resume_messages(
        self,
        prompt: str,
        partial: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not partial:
            return [{"role": "user", "content": prompt}]

        details = partial.get("reasoning_details")
        reasoning = str(partial.get("reasoning") or "")
        content = str(partial.get("content") or "")
        if details:
            # OpenRouter-style structured continuation: replay the provider-visible
            # reasoning_details unmodified, then explicitly ask for a complete answer.
            assistant: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                assistant["reasoning"] = reasoning
            assistant["reasoning_details"] = details
            return [
                {"role": "user", "content": prompt},
                assistant,
                {
                    "role": "user",
                    "content": (
                        "The previous response was interrupted after the assistant message above. "
                        "Continue using that exact provider-visible reasoning state, verify it, and "
                        "return a COMPLETE final response in the original requested format."
                    ),
                },
            ]

        total_limit = max(2_000, int(self.PARTIAL_RESUME_CHAR_LIMIT))
        reasoning = self._tail(reasoning, total_limit // 2)
        content = self._tail(content, total_limit // 2)
        resume_context = (
            "\n\n--- INTERRUPTED STEP: SOFT RESUME CONTEXT ---\n"
            "A previous API call for this exact step was interrupted. The text below is only the "
            "provider-visible partial work that arrived before interruption; hidden inference state "
            "cannot be restored. Reuse useful work, check it for mistakes, and continue efficiently. "
            "IMPORTANT: return a COMPLETE final response in the original requested format.\n\n"
            f"Previous model: {partial.get('model', '')}\n"
            f"Saved at: {partial.get('updated_at', '')}\n\n"
            "PARTIAL PROVIDER-VISIBLE REASONING:\n"
            f"{reasoning or '(none exposed)'}\n\n"
            "PARTIAL RESPONSE CONTENT:\n"
            f"{content or '(none yet)'}\n"
            "--- END SOFT RESUME CONTEXT ---"
        )
        return [{"role": "user", "content": prompt + resume_context}]

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
            "provider_base_url": getattr(agent.client, "base_url", ""),
            "reasoning_effort": agent.reasoning_effort,
            "original_prompt": prompt,
            "original_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "reasoning": str(buffers.get("reasoning") or ""),
            "reasoning_details": buffers.get("reasoning_details") or None,
            "content": str(buffers.get("content") or ""),
            "attempt": int(attempt),
            "updated_at": _now(),
        }
        self._partial_put(step_key, payload)
        buffers["last_flush"] = now_mono
        buffers["dirty_chars"] = 0

    def _stream_callback(
        self,
        agent: Agent,
        step_key: str,
        prompt: str,
        fingerprint: str,
        buffers: dict[str, Any],
        attempt: int,
    ):
        separator_added = {"value": False}

        def callback(channel: str, delta: Any) -> None:
            if isinstance(delta, str) and channel in {"reasoning", "content"}:
                if buffers.get("resumed") and not separator_added["value"]:
                    marker = f"\n\n--- RESUME ATTEMPT {attempt} ---\n"
                    buffers[channel] = str(buffers.get(channel) or "") + marker
                    separator_added["value"] = True
                buffers[channel] = str(buffers.get(channel) or "") + delta
                buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(delta)
            elif channel == "reasoning_details" and delta:
                details = buffers.setdefault("reasoning_details", [])
                if isinstance(delta, list):
                    details.extend(delta)
                else:
                    details.append(delta)
                try:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(
                        json.dumps(delta, ensure_ascii=False)
                    )
                except Exception:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + 1

            if channel in {"reasoning", "content", "reasoning_details"}:
                self._persist_partial(
                    agent=agent,
                    step_key=step_key,
                    prompt=prompt,
                    fingerprint=fingerprint,
                    buffers=buffers,
                    attempt=attempt,
                )

            self.trace.log(
                "agent_stream",
                agent=agent.name,
                model=agent.model,
                step_key=step_key,
                channel=channel,
                delta=delta,
            )
            try:
                self._check_stop()
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

        return callback

    def _call(self, agent: Agent, prompt: str, step_key: str) -> str:
        fingerprint = self._llm_fingerprint(agent, prompt)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            if cached.get("fingerprint") == fingerprint:
                self._partial_clear(step_key)
                self.trace.log(
                    "step_reused",
                    step_key=step_key,
                    agent=agent.name,
                    model=cached.get("model"),
                    fingerprint=fingerprint,
                )
                return str(cached.get("content", ""))
            self.trace.log(
                "cache_fingerprint_miss",
                step_key=step_key,
                kind="llm",
                legacy=not bool(cached.get("fingerprint")),
            )
            self._cache_delete(step_key)

        partial = self._partial_get(step_key)
        if partial and partial.get("fingerprint") != fingerprint:
            self.trace.log("partial_fingerprint_miss", step_key=step_key)
            self._partial_clear(step_key)

        self._check_stop()
        self._set_runtime(current_step=step_key)

        for attempt in range(1, self.max_retries + 1):
            partial = self._partial_get(step_key)
            messages = self._resume_messages(prompt, partial)
            buffers: dict[str, Any] = {
                "reasoning": str((partial or {}).get("reasoning") or ""),
                "reasoning_details": list((partial or {}).get("reasoning_details") or []),
                "content": str((partial or {}).get("content") or ""),
                "dirty_chars": 0,
                "last_flush": time.monotonic(),
                "resumed": bool(partial),
            }
            if partial:
                self.trace.log(
                    "partial_resume_loaded",
                    step_key=step_key,
                    agent=agent.name,
                    previous_model=partial.get("model"),
                    reasoning_chars=len(str(partial.get("reasoning") or "")),
                    reasoning_detail_count=len(partial.get("reasoning_details") or []),
                    content_chars=len(str(partial.get("content") or "")),
                    saved_at=partial.get("updated_at"),
                    attempt=attempt,
                    structured=bool(partial.get("reasoning_details")),
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
                        agent, step_key, prompt, fingerprint, buffers, attempt
                    ),
                )
                self.trace.agent_call(
                    agent.name,
                    response.model,
                    agent.temperature,
                    messages,
                    response,
                )
                self._cache_put(
                    step_key,
                    {
                        "status": "COMPLETE",
                        "fingerprint": fingerprint,
                        "content": content,
                        "model": response.model,
                        "reasoning_effort": agent.reasoning_effort,
                        "completed_at": _now(),
                        "soft_resumed": bool(partial),
                    },
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
                retry = _retryable(exc) and attempt < self.max_retries
                self.trace.log(
                    "agent_error",
                    agent=agent.name,
                    model=agent.model,
                    prompt=prompt,
                    step_key=step_key,
                    attempt=attempt,
                    retrying=retry,
                    partial_saved=bool(self._partial_get(step_key)),
                    error=repr(exc),
                )
                if not retry:
                    raise ResearchPaused(
                        f"{agent.name} / {agent.model} adımında hata: {exc}"
                    ) from exc
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

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        tool_name = str((request or {}).get("tool") or "none").strip().lower()
        if tool_name == "code_experiment":
            return super()._tool(request, step_key)

        fingerprint = self._tool_fingerprint(request)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            if cached.get("fingerprint") == fingerprint:
                raw = cached.get("result")
                if isinstance(raw, dict):
                    self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                    return ToolResult(
                        bool(raw.get("ok")),
                        str(raw.get("tool", "unknown")),
                        str(raw.get("output", "")),
                        str(raw.get("error", "")),
                        dict(raw.get("metadata", {}) or {}),
                    )
                return None
            self.trace.log("cache_fingerprint_miss", step_key=step_key, kind="tool")
            self._cache_delete(step_key)

        result = super()._tool(request, step_key)
        record = self._cache_get(step_key)
        if isinstance(record, dict) and record.get("status") == "COMPLETE":
            record = dict(record)
            record["fingerprint"] = fingerprint
            self._cache_put(step_key, record)
        return result

    def _search_literature(self, query: str, limit: int = 8) -> list[Paper]:
        fingerprint = content_fingerprint(
            "literature_search:v2",
            {"query": query.strip(), "limit": int(limit), "client": type(self.literature).__name__},
        )
        key = f"literature:search:{fingerprint[:20]}"
        cached = self._cache_get(key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            papers = []
            for raw in cached.get("papers", []):
                try:
                    papers.append(Paper(**raw))
                except TypeError:
                    continue
            self.trace.log("step_reused", step_key=key, records=len(papers), fingerprint=fingerprint)
            return papers

        self._check_stop()
        self.trace.log("literature_search_start", query=query, limit=limit, fingerprint=fingerprint)
        try:
            papers = self.literature.search(query, limit=limit)
            payload = [paper.as_dict() for paper in papers]
            self.trace.log("literature_search", query=query, results=payload, fingerprint=fingerprint)
            self._cache_put(
                key,
                {
                    "status": "COMPLETE",
                    "fingerprint": fingerprint,
                    "query": query,
                    "limit": int(limit),
                    "papers": payload,
                    "completed_at": _now(),
                },
            )
            return papers
        except Exception as exc:
            self.trace.log("literature_search_error", query=query, error=str(exc), fingerprint=fingerprint)
            return []
