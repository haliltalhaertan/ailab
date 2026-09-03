from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import httpx
from openai import OpenAI

from lab.reasoning_settings import normalize_effort

DEFAULT_MODEL = "openai/gpt-4o-mini"
StreamCallback = Callable[[str, Any], None]


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cost_usd: float | None = None
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    provider_reasoning: str = ""
    reasoning_details: Any = None
    request_messages: list[dict] | None = None
    requested_reasoning_effort: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _extra(obj: Any, name: str, default: Any = None) -> Any:
    value = getattr(obj, name, None)
    if value is not None:
        return value
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict) and name in extra:
        return extra[name]
    return default


def _detail_token_count(details: Any, field: str) -> int:
    if details is None:
        return 0
    value = _extra(details, field, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _usage_values(usage: Any) -> tuple[int, int, int, int, float | None]:
    prompt_tokens = int(_extra(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(_extra(usage, "completion_tokens", 0) or 0) if usage else 0
    prompt_details = _extra(usage, "prompt_tokens_details") if usage else None
    completion_details = _extra(usage, "completion_tokens_details") if usage else None
    reasoning_tokens = _detail_token_count(completion_details, "reasoning_tokens")
    cached_tokens = _detail_token_count(prompt_details, "cached_tokens")
    cost_raw = _extra(usage, "cost") if usage else None
    try:
        cost_usd = float(cost_raw) if cost_raw is not None else None
    except (TypeError, ValueError):
        cost_usd = None
    return prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost_usd


class LLMClient:
    """OpenAI-compatible client with *no hidden SDK retry layer*.

    Retry/backoff belongs to RunController/TheoremResearchLab where every retry is
    traceable. Setting OpenAI(max_retries=0) prevents the SDK from silently making
    extra requests that the research ledger cannot account for.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout_s: float | None = None,
    ):
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "API anahtarı bulunamadı. .env dosyasında OPENROUTER_API_KEY tanımla."
            )
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
        self.is_openrouter = "openrouter.ai" in self.base_url.lower()
        timeout = float(timeout_s or os.environ.get("LAB_LLM_TIMEOUT_S", "180"))
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            max_retries=0,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 30.0)),
        )

    def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stream_callback: StreamCallback | None = None,
    ) -> LLMResponse:
        model = model or os.environ.get("LAB_MODEL", DEFAULT_MODEL)
        reasoning_effort = normalize_effort(reasoning_effort)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if self.is_openrouter:
            reasoning: dict[str, Any] = {"exclude": False}
            if reasoning_effort is not None:
                reasoning["effort"] = reasoning_effort
            kwargs["extra_body"] = {
                "usage": {"include": True},
                "reasoning": reasoning,
            }

        if stream_callback is None:
            return self._complete_once(kwargs, messages, model, reasoning_effort)
        return self._complete_stream(kwargs, messages, model, reasoning_effort, stream_callback)

    def _complete_once(
        self,
        kwargs: dict[str, Any],
        messages: list[dict],
        model: str,
        reasoning_effort: str | None,
    ) -> LLMResponse:
        start = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        usage = resp.usage
        message = resp.choices[0].message
        prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost_usd = _usage_values(usage)
        provider_reasoning = _extra(message, "reasoning", "") or ""
        reasoning_details = _jsonable(_extra(message, "reasoning_details"))
        return LLMResponse(
            content=message.content or "",
            model=resp.model or model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=round(latency, 3),
            cost_usd=cost_usd,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            provider_reasoning=str(provider_reasoning),
            reasoning_details=reasoning_details,
            request_messages=[dict(m) for m in messages],
            requested_reasoning_effort=reasoning_effort,
        )

    def _complete_stream(
        self,
        kwargs: dict[str, Any],
        messages: list[dict],
        model: str,
        reasoning_effort: str | None,
        stream_callback: StreamCallback,
    ) -> LLMResponse:
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        stream_kwargs["stream_options"] = {"include_usage": True}

        start = time.perf_counter()
        stream = self._client.chat.completions.create(**stream_kwargs)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_details: list[Any] = []
        final_usage = None
        resolved_model = model

        for chunk in stream:
            if getattr(chunk, "model", None):
                resolved_model = chunk.model
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                final_usage = usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = choices[0].delta

            content_delta = _extra(delta, "content", "") or ""
            if content_delta:
                text = str(content_delta)
                content_parts.append(text)
                stream_callback("content", text)

            reasoning_delta = _extra(delta, "reasoning", "") or ""
            if reasoning_delta:
                text = str(reasoning_delta)
                reasoning_parts.append(text)
                stream_callback("reasoning", text)

            # Keep structured reasoning details for the final LLMResponse (and
            # therefore trace.jsonl / provider-resume), but do not duplicate
            # every detail delta into the high-volume live stream channel.
            detail_delta = _jsonable(_extra(delta, "reasoning_details"))
            if detail_delta:
                if isinstance(detail_delta, list):
                    reasoning_details.extend(detail_delta)
                else:
                    reasoning_details.append(detail_delta)

        latency = time.perf_counter() - start
        prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost_usd = _usage_values(final_usage)
        return LLMResponse(
            content="".join(content_parts),
            model=resolved_model or model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=round(latency, 3),
            cost_usd=cost_usd,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            provider_reasoning="".join(reasoning_parts),
            reasoning_details=reasoning_details or None,
            request_messages=[dict(m) for m in messages],
            requested_reasoning_effort=reasoning_effort,
        )


@lru_cache(maxsize=4)
def get_default_client(base_url: str | None = None) -> LLMClient:
    """Lazily create and share a connection pool per base URL.

    Importing/constructing Agent objects is now safe in offline tests; the API key
    is required only when an agent actually makes its first network call.
    """

    return LLMClient(base_url=base_url)
