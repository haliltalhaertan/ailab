from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


MODELS_URL = "https://openrouter.ai/api/v1/models?sort=most-popular"
CATALOG_CACHE_TTL_S = 6 * 60 * 60
CATALOG_CACHE_SCHEMA_VERSION = 1
_ALL_GATEWAY_REASONING_EFFORTS: tuple[str, ...] = ("max", "xhigh", "high", "medium", "low", "minimal", "none")


@dataclass(frozen=True)
class OpenRouterModel:
    id: str
    name: str
    context_length: int | None = None
    max_completion_tokens: int | None = None
    supported_parameters: tuple[str, ...] = ()
    reasoning_mandatory: bool = False
    reasoning_supported_efforts: tuple[str, ...] = ()
    reasoning_default_effort: str | None = None
    reasoning_supports_max_tokens: bool = False
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _reasoning_efforts(reasoning: Any) -> tuple[str, ...]:
    """Normalize catalog effort metadata while preserving absent-vs-null semantics.

    OpenRouter documents an omitted ``supported_efforts`` field as unknown/not
    selectable, but an explicit null as accepting all gateway effort values.
    Mandatory-reasoning models must never be sent ``effort=none``.
    """

    if not isinstance(reasoning, dict) or "supported_efforts" not in reasoning:
        return ()
    raw = reasoning.get("supported_efforts")
    if raw is None:
        efforts = _ALL_GATEWAY_REASONING_EFFORTS
    else:
        efforts = _string_tuple(raw)
    if bool(reasoning.get("mandatory", False)):
        return tuple(value for value in efforts if value.casefold() != "none")
    return efforts


def parse_models_payload(payload: dict[str, Any]) -> list[OpenRouterModel]:
    models: list[OpenRouterModel] = []
    for raw in payload.get("data", []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        pricing = raw.get("pricing") or {}
        top_provider = raw.get("top_provider") or {}
        reasoning_raw = raw.get("reasoning")
        reasoning = reasoning_raw if isinstance(reasoning_raw, dict) else {}
        models.append(
            OpenRouterModel(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                context_length=_optional_int(raw.get("context_length")),
                max_completion_tokens=_optional_int(top_provider.get("max_completion_tokens")),
                supported_parameters=_string_tuple(raw.get("supported_parameters")),
                reasoning_mandatory=bool(reasoning.get("mandatory", False)),
                reasoning_supported_efforts=_reasoning_efforts(reasoning_raw),
                reasoning_default_effort=(
                    str(reasoning.get("default_effort")).strip()
                    if reasoning.get("default_effort") not in (None, "")
                    else None
                ),
                reasoning_supports_max_tokens=bool(reasoning.get("supports_max_tokens", False)),
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
    """Fetch the live OpenRouter text-model catalog."""

    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    headers = {"Accept": "application/json", "User-Agent": "ailab/0.3"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(MODELS_URL, headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        payload = json.loads(response.read().decode("utf-8"))
    return parse_models_payload(payload)


def catalog_cache_path() -> Path:
    configured = str(os.environ.get("AILAB_CATALOG_CACHE_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "ailab" / "openrouter_models.json"


def _epoch_from_iso(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _model_from_cache(raw: Any) -> OpenRouterModel | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return OpenRouterModel(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        context_length=_optional_int(raw.get("context_length")),
        max_completion_tokens=_optional_int(raw.get("max_completion_tokens")),
        supported_parameters=_string_tuple(raw.get("supported_parameters")),
        reasoning_mandatory=bool(raw.get("reasoning_mandatory", False)),
        reasoning_supported_efforts=_string_tuple(raw.get("reasoning_supported_efforts")),
        reasoning_default_effort=(
            str(raw.get("reasoning_default_effort")).strip()
            if raw.get("reasoning_default_effort") not in (None, "")
            else None
        ),
        reasoning_supports_max_tokens=bool(raw.get("reasoning_supports_max_tokens", False)),
        prompt_usd_per_million=(
            float(raw["prompt_usd_per_million"])
            if raw.get("prompt_usd_per_million") is not None
            else None
        ),
        completion_usd_per_million=(
            float(raw["completion_usd_per_million"])
            if raw.get("completion_usd_per_million") is not None
            else None
        ),
    )


def _read_disk_cache(path: Path) -> tuple[list[OpenRouterModel], float] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or int(raw.get("schema_version", 0) or 0) != CATALOG_CACHE_SCHEMA_VERSION:
        return None
    fetched_epoch = _epoch_from_iso(raw.get("fetched_at"))
    if fetched_epoch is None:
        return None
    models = [model for item in raw.get("models", []) if (model := _model_from_cache(item)) is not None]
    return models, fetched_epoch


def _write_disk_cache(path: Path, models: list[OpenRouterModel], fetched_epoch: float) -> None:
    payload = {
        "schema_version": CATALOG_CACHE_SCHEMA_VERSION,
        "fetched_at": datetime.fromtimestamp(fetched_epoch, tz=timezone.utc).isoformat(),
        "models": [model.as_dict() for model in models],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


_MEMORY_CACHE: tuple[float, tuple[OpenRouterModel, ...], str] | None = None


def clear_catalog_memory_cache() -> None:
    global _MEMORY_CACHE
    _MEMORY_CACHE = None


def cached_openrouter_models(
    api_key: str | None = None,
    *,
    timeout: float = 15.0,
    ttl_s: float = CATALOG_CACHE_TTL_S,
    cache_path: Path | None = None,
    fetcher: Callable[..., list[OpenRouterModel]] | None = None,
    now: float | None = None,
) -> tuple[list[OpenRouterModel], str]:
    """Return catalog models with memory/disk/network/stale fail-open caching."""

    global _MEMORY_CACHE
    current = time.time() if now is None else float(now)
    if _MEMORY_CACHE is not None:
        fetched_epoch, cached_models, origin = _MEMORY_CACHE
        if current - fetched_epoch < ttl_s:
            return list(cached_models), "memory" if cached_models else origin

    path = cache_path or catalog_cache_path()
    disk = _read_disk_cache(path)
    stale_models: list[OpenRouterModel] = []
    stale_epoch: float | None = None
    if disk is not None:
        disk_models, disk_epoch = disk
        if current - disk_epoch < ttl_s:
            _MEMORY_CACHE = (disk_epoch, tuple(disk_models), "disk")
            return disk_models, "disk"
        stale_models, stale_epoch = disk_models, disk_epoch

    loader = fetcher or fetch_openrouter_models
    try:
        loaded_models = loader(api_key=api_key, timeout=timeout)
    except Exception:
        if stale_epoch is not None:
            _MEMORY_CACHE = (current, tuple(stale_models), "stale_disk")
            return stale_models, "stale_disk"
        _MEMORY_CACHE = (current, (), "unavailable")
        return [], "unavailable"

    try:
        _write_disk_cache(path, loaded_models, current)
    except OSError:
        pass
    _MEMORY_CACHE = (current, tuple(loaded_models), "network")
    return loaded_models, "network"


def lookup_openrouter_model(
    model_id: str,
    **kwargs: Any,
) -> tuple[OpenRouterModel | None, str]:
    """Look up one model by exact OpenRouter model ID."""

    models, source = cached_openrouter_models(**kwargs)
    wanted = str(model_id or "").strip()
    for model in models:
        if model.id == wanted:
            return model, source
    return None, source
