from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import httpx
from openai import OpenAI

from lab.openrouter_catalog import OpenRouterModel, lookup_openrouter_model
from lab.reasoning_settings import normalize_effort

DEFAULT_MODEL = "openai/gpt-4o-mini"
FINAL_ANSWER_RESERVE = 8192
StreamCallback = Callable[[str, Any], None]

_EFFORT_ORDER = {
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
}


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
    finish_reason: str | None = None
    requested_max_tokens: int | None = None
    model_max_completion_tokens: int | None = None
    max_tokens_source: str = "provider_default"
    catalog_source: str = "unavailable"
    reasoning_effort_sent: str | None = None
    effort_resolution: str = "provider_default"
    reasoning_max_tokens_sent: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class _RequestPolicy:
    requested_max_tokens: int | None
    model_max_completion_tokens: int | None
    max_tokens_source: str
    catalog_source: str
    reasoning_effort_requested: str | None
    reasoning_effort_sent: str | None
    effort_resolution: str
    reasoning_max_tokens_sent: int | None


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


def _emergency_max_tokens() -> int | None:
    """Return the optional global runaway-cost ceiling.

    The value can only narrow an already-known request. It never manufactures a
    completion limit for a model whose capacity is unknown.
    """

    raw = str(os.environ.get("LAB_EMERGENCY_MAX_TOKENS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("LAB_EMERGENCY_MAX_TOKENS must be a positive integer") from exc
    if value <= 0:
        raise ValueError("LAB_EMERGENCY_MAX_TOKENS must be a positive integer")
    return value


def _requested_max_tokens(
    explicit: int | None,
    model_max_completion_tokens: int | None = None,
) -> tuple[int | None, str]:
    """Resolve an effective completion request without inventing model capacity."""

    requested: int | None
    source: str
    explicit_value: int | None = None
    if explicit is not None:
        candidate = int(explicit)
        if candidate > 0:
            explicit_value = candidate

    if explicit_value is not None:
        requested = explicit_value
        source = "explicit"
    elif model_max_completion_tokens is not None and int(model_max_completion_tokens) > 0:
        requested = int(model_max_completion_tokens)
        source = "catalog"
    else:
        requested = None
        source = "provider_default"

    if (
        requested is not None
        and model_max_completion_tokens is not None
        and requested > int(model_max_completion_tokens)
    ):
        requested = int(model_max_completion_tokens)
        source += "+model_clamp"

    emergency = _emergency_max_tokens()
    if emergency is not None and requested is not None:
        requested = min(requested, emergency)
        source += "+emergency"
    return requested, source


def _resolve_reasoning_effort(
    requested: str | None,
    model: OpenRouterModel | None,
) -> tuple[str | None, str]:
    if requested in {None, ""}:
        return None, "provider_default"
    requested = str(requested).casefold()
    if model is None:
        return None, "catalog_unknown"

    supported = tuple(str(value).casefold() for value in model.reasoning_supported_efforts)
    if not supported:
        return None, "catalog_unknown"
    if requested in supported:
        return requested, "exact"
    requested_rank = _EFFORT_ORDER.get(requested)
    if requested_rank is None:
        return None, "unsupported"
    lower = [
        value
        for value in supported
        if value in _EFFORT_ORDER and _EFFORT_ORDER[value] < requested_rank
    ]
    if not lower:
        return None, "unsupported_no_lower"
    return max(lower, key=lambda value: _EFFORT_ORDER[value]), "lower_supported"


def _reasoning_request(
    model: OpenRouterModel | None,
    requested_max_tokens: int | None,
    reasoning_effort: str | None,
) -> tuple[dict[str, Any], str | None, str, int | None]:
    """Build one OpenRouter reasoning control without mixing effort and token budget."""

    if (
        model is not None
        and model.reasoning_supports_max_tokens
        and requested_max_tokens is not None
        and requested_max_tokens >= FINAL_ANSWER_RESERVE + 1024
    ):
        reasoning_max = max(requested_max_tokens - FINAL_ANSWER_RESERVE, 1024)
        return {"max_tokens": reasoning_max, "exclude": False}, None, "reasoning_max_tokens", reasoning_max

    effort_sent, resolution = _resolve_reasoning_effort(reasoning_effort, model)
    if effort_sent is not None:
        return {"effort": effort_sent, "exclude": False}, effort_sent, resolution, None
    return {"exclude": False}, None, resolution, None


def next_lower_supported_effort(model_id: str | None, current_effort: str | None) -> str | None:
    """Return the next lower catalog-supported effort, otherwise preserve current."""

    current = str(current_effort or "").casefold()
    if not current or current not in _EFFORT_ORDER or not model_id:
        return current_effort
    model, _source = lookup_openrouter_model(str(model_id))
    if model is None or not model.reasoning_supported_efforts:
        return current_effort
    supported = [
        str(value).casefold()
        for value in model.reasoning_supported_efforts
        if str(value).casefold() in _EFFORT_ORDER
    ]
    lower = [value for value in supported if _EFFORT_ORDER[value] < _EFFORT_ORDER[current]]
    if not lower:
        return current_effort
    return max(lower, key=lambda value: _EFFORT_ORDER[value])


class LLMClient:
    """OpenAI-compatible client with *no hidden SDK retry layer*."""

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

        catalog_model: OpenRouterModel | None = None
        catalog_source = "unavailable"
        if self.is_openrouter:
            catalog_model, catalog_source = lookup_openrouter_model(model)
        model_max = catalog_model.max_completion_tokens if catalog_model is not None else None
        requested_max_tokens, max_tokens_source = _requested_max_tokens(max_tokens, model_max)
        reasoning: dict[str, Any] = {}
        effort_sent: str | None = None
        effort_resolution = "not_openrouter"
        reasoning_max_tokens_sent: int | None = None
        if self.is_openrouter:
            reasoning, effort_sent, effort_resolution, reasoning_max_tokens_sent = _reasoning_request(
                catalog_model,
                requested_max_tokens,
                reasoning_effort,
            )

        policy = _RequestPolicy(
            requested_max_tokens=requested_max_tokens,
            model_max_completion_tokens=model_max,
            max_tokens_source=max_tokens_source,
            catalog_source=catalog_source,
            reasoning_effort_requested=reasoning_effort,
            reasoning_effort_sent=effort_sent,
            effort_resolution=effort_resolution,
            reasoning_max_tokens_sent=reasoning_max_tokens_sent,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if requested_max_tokens is not None:
            kwargs["max_tokens"] = requested_max_tokens
        if self.is_openrouter:
            kwargs["extra_body"] = {
                "usage": {"include": True},
                "reasoning": reasoning,
            }

        if stream_callback is None:
            return self._complete_once(kwargs, messages, model, policy)
        return self._complete_stream(kwargs, messages, model, policy, stream_callback)

    @staticmethod
    def _response_kwargs(policy: _RequestPolicy) -> dict[str, Any]:
        return {
            "requested_reasoning_effort": policy.reasoning_effort_requested,
            "requested_max_tokens": policy.requested_max_tokens,
            "model_max_completion_tokens": policy.model_max_completion_tokens,
            "max_tokens_source": policy.max_tokens_source,
            "catalog_source": policy.catalog_source,
            "reasoning_effort_sent": policy.reasoning_effort_sent,
            "effort_resolution": policy.effort_resolution,
            "reasoning_max_tokens_sent": policy.reasoning_max_tokens_sent,
        }

    def _complete_once(
        self,
        kwargs: dict[str, Any],
        messages: list[dict],
        model: str,
        policy: _RequestPolicy,
    ) -> LLMResponse:
        start = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        usage = resp.usage
        choice = resp.choices[0]
        message = choice.message
        prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost_usd = _usage_values(usage)
        provider_reasoning = _extra(message, "reasoning", "") or ""
        reasoning_details = _jsonable(_extra(message, "reasoning_details"))
        finish_reason_raw = _extra(choice, "finish_reason")
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
            finish_reason=str(finish_reason_raw) if finish_reason_raw is not None else None,
            **self._response_kwargs(policy),
        )

    def _complete_stream(
        self,
        kwargs: dict[str, Any],
        messages: list[dict],
        model: str,
        policy: _RequestPolicy,
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
        finish_reason: str | None = None

        for chunk in stream:
            if getattr(chunk, "model", None):
                resolved_model = chunk.model
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                final_usage = usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            finish_raw = _extra(choice, "finish_reason")
            if finish_raw is not None:
                finish_reason = str(finish_raw)
            delta = choice.delta

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
            finish_reason=finish_reason,
            **self._response_kwargs(policy),
        )


@lru_cache(maxsize=4)
def get_default_client(base_url: str | None = None) -> LLMClient:
    return LLMClient(base_url=base_url)
