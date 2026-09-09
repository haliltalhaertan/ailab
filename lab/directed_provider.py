"""One client per call; SDK retries disabled so all attempts use the coordinator budget."""

from __future__ import annotations

import os
import time
import json

from openai import OpenAI

from lab.client import _extra, _jsonable, _usage_values
from lab.directed_gate import redact


class ProviderStopped(RuntimeError):
    pass


def _safe_structured(value):
    """Redact strings after conversion, preserving provider-visible structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_safe_structured(item) for item in value]
    if isinstance(value, dict):
        return {redact(key): _safe_structured(item) for key, item in value.items()}
    return value


class DirectedProvider:
    identity = "openai-compatible-v1"

    def call(self, lane, prompt: str, callback, cancelled, timeout: float, step_id: str) -> dict:
        if cancelled():
            raise ProviderStopped("Stop before dispatch")
        started = time.monotonic()
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Provider credential unavailable")
        base = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        kwargs = {
            "model": lane.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": lane.max_completion_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_headers": {"Idempotency-Key": step_id},
        }
        reasoning_cap = getattr(lane, "max_reasoning_tokens", None)
        if reasoning_cap is not None:
            if "openrouter.ai" not in base:
                raise ValueError("Numeric reasoning budget requires the OpenRouter adapter")
            kwargs["extra_body"] = {"reasoning": {"max_tokens": reasoning_cap}}
        elif lane.reasoning_effort is not None:
            if "openrouter.ai" in base:
                kwargs["extra_body"] = {"reasoning": {"effort": lane.reasoning_effort}}
            else:
                kwargs["reasoning_effort"] = lane.reasoning_effort
        text, reasoning, finish, usage = "", "", None, {}
        metadata = {}
        reasoning_details = []
        detail_characters = 0
        with OpenAI(api_key=key, base_url=base, max_retries=0, timeout=min(timeout, 60)) as client:
            with client.chat.completions.create(**kwargs) as stream:
                for event in stream:
                    # Persist identifiers before stop checks: a billable generation may
                    # already exist even when cancellation meets the first chunk.
                    incoming = {
                        name: redact(value[:512])
                        for name in ("id", "model", "provider")
                        if isinstance(value := _extra(event, name), str) and value
                    }
                    if any(metadata.get(name) != value for name, value in incoming.items()):
                        metadata.update(incoming)
                        callback("metadata", dict(metadata))
                    if event.usage:
                        p, c, r, cached, cost = _usage_values(event.usage)
                        usage = {
                            "prompt_tokens": p,
                            "completion_tokens": c,
                            "reasoning_tokens": r,
                            "cached_tokens": cached,
                            "cost_usd": cost,
                        }
                        callback("usage", dict(usage))
                    if cancelled():
                        raise ProviderStopped("Stop during provider stream")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("Lane wall-time ceiling")
                    for choice in event.choices:
                        if choice.finish_reason:
                            finish = choice.finish_reason
                        delta = choice.delta
                        if delta.content:
                            text += delta.content
                            callback("content", redact(delta.content))
                        fragment = _extra(delta, "reasoning") or _extra(delta, "reasoning_content")
                        if fragment:
                            reasoning += str(fragment)
                            callback("reasoning", redact(str(fragment)))
                        details = _jsonable(_extra(delta, "reasoning_details"))
                        if details is not None:
                            detail_characters += len(json.dumps(details, ensure_ascii=False))
                            safe_details = _safe_structured(details)
                            reasoning_details.extend(safe_details if isinstance(safe_details, list) else [safe_details])
                            callback("reasoning_details", safe_details)
                    if len(text) + len(reasoning) + detail_characters > 2_000_000:
                        raise RuntimeError("Provider output ceiling")
        return {
            "content": redact(text),
            "provider_reasoning": redact(reasoning),
            "reasoning_details": reasoning_details or None,
            "usage": usage,
            "metadata": metadata,
            "finish_reason": finish,
            "complete": finish == "stop",
        }
