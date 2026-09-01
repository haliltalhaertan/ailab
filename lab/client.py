import json
import os
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

DEFAULT_MODEL = "openai/gpt-4o-mini"


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


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "API anahtarı bulunamadı. .env dosyasında OPENROUTER_API_KEY tanımla."
            )
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
        self.is_openrouter = "openrouter.ai" in self.base_url.lower()
        self._client = OpenAI(api_key=api_key, base_url=self.base_url)

    def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        model = model or os.environ.get("LAB_MODEL", DEFAULT_MODEL)
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if self.is_openrouter:
            kwargs["extra_body"] = {"usage": {"include": True}}

        start = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        usage = resp.usage
        message = resp.choices[0].message

        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        cost_raw = _extra(usage, "cost") if usage else None
        try:
            cost_usd = float(cost_raw) if cost_raw is not None else None
        except (TypeError, ValueError):
            cost_usd = None

        prompt_details = getattr(usage, "prompt_tokens_details", None) if usage else None
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        provider_reasoning = _extra(message, "reasoning", "") or ""
        reasoning_details = _jsonable(_extra(message, "reasoning_details"))

        return LLMResponse(
            content=message.content or "",
            model=resp.model or model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=round(latency, 3),
            cost_usd=cost_usd,
            reasoning_tokens=_detail_token_count(completion_details, "reasoning_tokens"),
            cached_tokens=_detail_token_count(prompt_details, "cached_tokens"),
            provider_reasoning=str(provider_reasoning),
            reasoning_details=reasoning_details,
            request_messages=[dict(m) for m in messages],
        )
