import os
import time
from dataclasses import dataclass

from openai import OpenAI

DEFAULT_MODEL = "openai/gpt-4o-mini"


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "API anahtarı bulunamadı. .env dosyasında OPENROUTER_API_KEY tanımla."
            )
        base_url = base_url or os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
        self._client = OpenAI(api_key=api_key, base_url=base_url)

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
        start = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=resp.model or model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_s=round(latency, 3),
        )
