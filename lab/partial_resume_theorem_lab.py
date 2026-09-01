from __future__ import annotations

import time
from typing import Any

from lab.agent import Agent
from lab.resumable_theorem_lab import (
    ResearchPaused,
    ResearchStopped,
    TheoremResearchLab as BaseTheoremResearchLab,
    _atomic_json,
    _now,
    _read_json,
    _retryable,
)


class TheoremResearchLab(BaseTheoremResearchLab):
    """The theorem lab with durable partial-response soft resume.

    Completed steps are still reused from ``step_cache.json``. In addition, an
    interrupted in-flight LLM step stores provider-visible stream fragments in
    ``partial_steps.json``. On retry/resume those fragments are supplied as
    context to a fresh API request so the model can reuse the work already done.

    This is deliberately a *soft* resume: provider KV-cache / hidden inference
    state is not available through ordinary OpenRouter requests.
    """

    PARTIAL_RESUME_CHAR_LIMIT = 60_000
    PARTIAL_FLUSH_INTERVAL_S = 0.25
    PARTIAL_FLUSH_CHARS = 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.partial_path = self.state.root / "partial_steps.json"

    def _partials(self) -> dict[str, Any]:
        value = _read_json(self.partial_path, {})
        return value if isinstance(value, dict) else {}

    def _partial_get(self, step_key: str) -> dict[str, Any] | None:
        value = self._partials().get(step_key)
        return value if isinstance(value, dict) else None

    def _partial_put(self, step_key: str, value: dict[str, Any]) -> None:
        partials = self._partials()
        partials[step_key] = value
        _atomic_json(self.partial_path, partials)

    def _partial_clear(self, step_key: str) -> None:
        partials = self._partials()
        if step_key not in partials:
            return
        partials.pop(step_key, None)
        _atomic_json(self.partial_path, partials)
        self.trace.log("partial_step_cleared", step_key=step_key)

    @staticmethod
    def _tail(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return "[... önceki kısım context limiti için kırpıldı ...]\n" + text[-limit:]

    def _resume_messages(self, prompt: str, partial: dict[str, Any] | None) -> list[dict[str, str]]:
        if not partial:
            return [{"role": "user", "content": prompt}]

        total_limit = max(2_000, int(self.PARTIAL_RESUME_CHAR_LIMIT))
        reasoning = str(partial.get("reasoning") or "")
        content = str(partial.get("content") or "")
        # Split the budget so a huge reasoning stream cannot evict the visible answer.
        reasoning = self._tail(reasoning, total_limit // 2)
        content = self._tail(content, total_limit // 2)
        resume_context = (
            "\n\n--- INTERRUPTED STEP: SOFT RESUME CONTEXT ---\n"
            "A previous API call for this exact step was interrupted. The text below is only the "
            "provider-visible partial work that arrived before interruption; hidden inference state "
            "cannot be restored. Reuse useful work, check it for mistakes, and continue efficiently. "
            "IMPORTANT: return a COMPLETE final response in the original requested format; do not "
            "return only the missing suffix.\n\n"
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
        buffers: dict[str, Any],
        attempt: int,
        force: bool = False,
    ) -> None:
        now_mono = time.monotonic()
        dirty = int(buffers.get("dirty_chars", 0) or 0)
        last_flush = float(buffers.get("last_flush", 0.0) or 0.0)
        if not force and dirty < self.PARTIAL_FLUSH_CHARS and now_mono - last_flush < self.PARTIAL_FLUSH_INTERVAL_S:
            return
        if not buffers.get("reasoning") and not buffers.get("content"):
            return
        payload = {
            "status": "PARTIAL",
            "agent": agent.name,
            "model": agent.model,
            "original_prompt": prompt,
            "reasoning": str(buffers.get("reasoning") or ""),
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
                self._persist_partial(
                    agent=agent,
                    step_key=step_key,
                    prompt=prompt,
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
                    buffers=buffers,
                    attempt=attempt,
                    force=True,
                )
                raise

        return callback

    def _call(self, agent: Agent, prompt: str, step_key: str) -> str:
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            self._partial_clear(step_key)
            self.trace.log(
                "step_reused",
                step_key=step_key,
                agent=agent.name,
                model=cached.get("model"),
            )
            return str(cached.get("content", ""))

        self._check_stop()
        self._set_runtime(current_step=step_key)

        for attempt in range(1, self.max_retries + 1):
            partial = self._partial_get(step_key)
            messages = self._resume_messages(prompt, partial)
            buffers: dict[str, Any] = {
                "reasoning": str((partial or {}).get("reasoning") or ""),
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
                    content_chars=len(str(partial.get("content") or "")),
                    saved_at=partial.get("updated_at"),
                    attempt=attempt,
                )

            self.trace.log(
                "agent_start",
                agent=agent.name,
                model=agent.model,
                temperature=agent.temperature,
                system_prompt=agent.system_prompt,
                prompt=prompt,
                step_key=step_key,
                attempt=attempt,
                soft_resume=bool(partial),
            )
            try:
                content, response = agent.respond(
                    messages,
                    stream_callback=self._stream_callback(
                        agent, step_key, prompt, buffers, attempt
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
                        "content": content,
                        "model": response.model,
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
