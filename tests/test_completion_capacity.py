from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lab.client import (
    FINAL_ANSWER_RESERVE,
    LLMClient,
    _reasoning_request,
    _requested_max_tokens,
    _resolve_reasoning_effort,
)
from lab.openrouter_catalog import (
    OpenRouterModel,
    cached_openrouter_models,
    clear_catalog_memory_cache,
    lookup_openrouter_model,
    parse_models_payload,
)


def _model(**overrides):
    values = {
        "id": "test/model",
        "name": "Test",
        "max_completion_tokens": 384000,
        "supported_parameters": ("reasoning_effort", "reasoning"),
        "reasoning_supported_efforts": ("high", "xhigh"),
    }
    values.update(overrides)
    return OpenRouterModel(**values)


def test_catalog_parses_completion_and_reasoning_capabilities():
    payload = {
        "data": [
            {
                "id": "deepseek/test",
                "name": "DeepSeek Test",
                "context_length": 500000,
                "supported_parameters": ["reasoning", "reasoning_effort", "max_tokens"],
                "top_provider": {"max_completion_tokens": 384000},
                "reasoning": {
                    "mandatory": True,
                    "supported_efforts": ["high", "xhigh"],
                    "default_effort": "high",
                },
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            },
            {
                "id": "plain/test",
                "name": "Plain",
                "top_provider": {"max_completion_tokens": 65536},
                "reasoning": {},
            },
        ]
    }

    first, second = parse_models_payload(payload)
    assert first.max_completion_tokens == 384000
    assert first.supported_parameters == ("reasoning", "reasoning_effort", "max_tokens")
    assert first.reasoning_mandatory is True
    assert first.reasoning_supported_efforts == ("high", "xhigh")
    assert first.reasoning_default_effort == "high"
    assert first.reasoning_supports_max_tokens is False
    assert second.reasoning_supports_max_tokens is False


def test_catalog_memory_and_disk_cache_avoid_repeated_network(tmp_path: Path):
    clear_catalog_memory_cache()
    path = tmp_path / "catalog.json"
    calls = {"count": 0}

    def fetcher(**kwargs):
        calls["count"] += 1
        return [_model()]

    models, source = cached_openrouter_models(
        cache_path=path,
        fetcher=fetcher,
        now=1000,
    )
    assert source == "network"
    assert models[0].id == "test/model"
    assert calls["count"] == 1

    models, source = cached_openrouter_models(
        cache_path=path,
        fetcher=fetcher,
        now=1001,
    )
    assert source == "memory"
    assert calls["count"] == 1

    clear_catalog_memory_cache()
    models, source = cached_openrouter_models(
        cache_path=path,
        fetcher=fetcher,
        now=1002,
    )
    assert source == "disk"
    assert calls["count"] == 1


def test_catalog_network_failure_uses_stale_disk_then_unavailable(tmp_path: Path):
    path = tmp_path / "catalog.json"
    clear_catalog_memory_cache()
    cached_openrouter_models(cache_path=path, fetcher=lambda **kwargs: [_model()], now=1000, ttl_s=10)
    clear_catalog_memory_cache()

    def fail(**kwargs):
        raise OSError("offline")

    models, source = cached_openrouter_models(
        cache_path=path,
        fetcher=fail,
        now=2000,
        ttl_s=10,
    )
    assert source == "stale_disk"
    assert models[0].id == "test/model"

    clear_catalog_memory_cache()
    missing = tmp_path / "missing.json"
    models, source = cached_openrouter_models(
        cache_path=missing,
        fetcher=fail,
        now=2000,
        ttl_s=10,
    )
    assert models == []
    assert source == "unavailable"


def test_catalog_disk_write_failure_is_fail_open(tmp_path: Path, monkeypatch):
    clear_catalog_memory_cache()
    monkeypatch.setattr(
        "lab.openrouter_catalog._write_disk_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only cache")),
    )

    models, source = cached_openrouter_models(
        cache_path=tmp_path / "catalog.json",
        fetcher=lambda **kwargs: [_model()],
        now=1000,
    )

    assert source == "network"
    assert models[0].id == "test/model"


def test_catalog_cache_is_atomic_json_and_lookup_is_exact(tmp_path: Path):
    clear_catalog_memory_cache()
    path = tmp_path / "catalog.json"
    cached_openrouter_models(cache_path=path, fetcher=lambda **kwargs: [_model()], now=1000)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["fetched_at"]
    assert not list(tmp_path.glob("*.tmp"))

    clear_catalog_memory_cache()
    exact, _ = lookup_openrouter_model(
        "test/model",
        cache_path=path,
        fetcher=lambda **kwargs: (_ for _ in ()).throw(AssertionError("network")),
        now=1001,
    )
    fuzzy, _ = lookup_openrouter_model(
        "model",
        cache_path=path,
        fetcher=lambda **kwargs: (_ for _ in ()).throw(AssertionError("network")),
        now=1001,
    )
    assert exact is not None
    assert fuzzy is None


def test_completion_limit_resolution_priority(monkeypatch):
    monkeypatch.delenv("LAB_EMERGENCY_MAX_TOKENS", raising=False)
    assert _requested_max_tokens(10000, 20000) == (10000, "explicit")
    assert _requested_max_tokens(None, 20000) == (20000, "catalog")
    assert _requested_max_tokens(None, None) == (None, "provider_default")
    assert _requested_max_tokens(30000, 20000) == (20000, "explicit+model_clamp")

    monkeypatch.setenv("LAB_EMERGENCY_MAX_TOKENS", "9000")
    assert _requested_max_tokens(None, 20000) == (9000, "catalog+emergency")
    assert _requested_max_tokens(None, None) == (None, "provider_default")


def test_reasoning_request_uses_max_tokens_or_effort_never_both():
    token_model = _model(
        reasoning_supports_max_tokens=True,
        reasoning_supported_efforts=("high", "xhigh"),
    )
    body, sent, resolution, reasoning_max = _reasoning_request(token_model, 384000, "high")
    assert body == {"max_tokens": 384000 - FINAL_ANSWER_RESERVE, "exclude": False}
    assert "effort" not in body
    assert sent is None
    assert resolution == "reasoning_max_tokens"
    assert reasoning_max == 384000 - FINAL_ANSWER_RESERVE

    effort_model = _model(reasoning_supports_max_tokens=False)
    body, sent, resolution, reasoning_max = _reasoning_request(effort_model, 384000, "high")
    assert body == {"effort": "high", "exclude": False}
    assert "max_tokens" not in body
    assert sent == "high"
    assert resolution == "exact"
    assert reasoning_max is None

    unknown, sent, resolution, reasoning_max = _reasoning_request(None, 384000, "high")
    assert unknown == {"exclude": False}
    assert sent is None
    assert reasoning_max is None
    assert resolution == "catalog_unknown"


def test_reasoning_small_limit_does_not_force_reasoning_token_budget():
    token_model = _model(reasoning_supports_max_tokens=True)
    body, _, _, reasoning_max = _reasoning_request(
        token_model,
        FINAL_ANSWER_RESERVE + 100,
        "high",
    )
    assert "max_tokens" not in body
    assert body.get("effort") == "high"
    assert reasoning_max is None


def test_effort_resolution_never_silently_escalates():
    glm = _model(reasoning_supported_efforts=("low", "high", "max"))
    assert _resolve_reasoning_effort("medium", glm) == ("low", "lower_supported")

    deepseek = _model(reasoning_supported_efforts=("high", "xhigh"))
    assert _resolve_reasoning_effort("medium", deepseek) == (None, "unsupported_no_lower")
    assert _resolve_reasoning_effort("high", deepseek) == ("high", "exact")

    gemini = _model(reasoning_supported_efforts=("low", "medium", "high"))
    assert _resolve_reasoning_effort("high", gemini) == ("high", "exact")


def test_complete_sends_catalog_limit_and_reasoning_shape(monkeypatch):
    model = _model()
    monkeypatch.setattr("lab.client.lookup_openrouter_model", lambda model_id: (model, "memory"))
    monkeypatch.delenv("LAB_EMERGENCY_MAX_TOKENS", raising=False)

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="test/model",
                usage=SimpleNamespace(
                    prompt_tokens=1,
                    completion_tokens=2,
                    prompt_tokens_details=None,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
                    cost=0.01,
                ),
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="{}", reasoning="r", reasoning_details=None),
                )],
            )

    client = LLMClient.__new__(LLMClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client.base_url = "https://openrouter.ai/api/v1"
    client.is_openrouter = True

    response = client.complete(
        [{"role": "user", "content": "p"}],
        model="test/model",
        reasoning_effort="high",
    )

    assert captured["max_tokens"] == 384000
    assert captured["extra_body"]["reasoning"] == {"effort": "high", "exclude": False}
    assert response.requested_max_tokens == 384000
    assert response.model_max_completion_tokens == 384000
    assert response.max_tokens_source == "catalog"
    assert response.catalog_source == "memory"
