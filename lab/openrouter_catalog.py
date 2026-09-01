from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any
from urllib.request import Request, urlopen


MODELS_URL = "https://openrouter.ai/api/v1/models?sort=most-popular"


@dataclass(frozen=True)
class OpenRouterModel:
    id: str
    name: str
    context_length: int | None = None
    prompt_usd_per_million: float | None = None
    completion_usd_per_million: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def label(self) -> str:
        price = []
        if self.prompt_usd_per_million is not None:
            price.append(f"in ${self.prompt_usd_per_million:g}/M")
        if self.completion_usd_per_million is not None:
            price.append(f"out ${self.completion_usd_per_million:g}/M")
        suffix = f" — {' · '.join(price)}" if price else ""
        return f"{self.name} ({self.id}){suffix}"


def _per_million(value: Any) -> float | None:
    if value in (None, "", "-1"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return round(number * 1_000_000, 9)


def parse_models_payload(payload: dict[str, Any]) -> list[OpenRouterModel]:
    models: list[OpenRouterModel] = []
    for raw in payload.get("data", []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        pricing = raw.get("pricing") or {}
        models.append(
            OpenRouterModel(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                context_length=(
                    int(raw["context_length"])
                    if raw.get("context_length") is not None
                    else None
                ),
                prompt_usd_per_million=_per_million(pricing.get("prompt")),
                completion_usd_per_million=_per_million(pricing.get("completion")),
            )
        )
    return models


def fetch_openrouter_models(
    api_key: str | None = None,
    *,
    timeout: float = 15.0,
) -> list[OpenRouterModel]:
    """Fetch the live OpenRouter text-model catalog.

    The endpoint returns current model slugs and pricing. The API key is optional
    for public catalog access, but is sent when available so the behavior matches
    the user's OpenRouter account.
    """

    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    headers = {"Accept": "application/json", "User-Agent": "ailab/0.2"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(MODELS_URL, headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        payload = json.loads(response.read().decode("utf-8"))
    return parse_models_payload(payload)
