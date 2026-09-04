import json
from types import SimpleNamespace

from lab.budget import budget_snapshot, expected_token_range
from lab.client import LLMClient, LLMResponse
from lab.trace import Trace


class _CapturingCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            model="fake/model",
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=2,
                prompt_tokens_details=None,
                completion_tokens_details=None,
                cost=None,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", reasoning="", reasoning_details=None),
                )
            ],
        )


class _FakeOpenAI:
    def __init__(self):
        self.completions = _CapturingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _client() -> tuple[LLMClient, _FakeOpenAI]:
    client = LLMClient.__new__(LLMClient)
    fake = _FakeOpenAI()
    client._client = fake
    client.base_url = "https://example.invalid/v1"
    client.is_openrouter = False
    return client, fake


def test_emergency_max_tokens_is_absent_by_default(monkeypatch):
    monkeypatch.delenv("LAB_EMERGENCY_MAX_TOKENS", raising=False)
    client, fake = _client()

    response = client.complete([{"role": "user", "content": "p"}], model="fake/model")

    assert "max_tokens" not in fake.completions.kwargs
    assert response.requested_max_tokens is None
    assert response.finish_reason == "stop"


def test_emergency_ceiling_does_not_manufacture_unknown_model_limit(monkeypatch):
    monkeypatch.setenv("LAB_EMERGENCY_MAX_TOKENS", "9000")
    client, fake = _client()

    response = client.complete([{"role": "user", "content": "p"}], model="fake/model")

    assert "max_tokens" not in fake.completions.kwargs
    assert response.requested_max_tokens is None
    assert response.max_tokens_source == "provider_default"


def test_emergency_ceiling_only_narrows_an_explicit_cap(monkeypatch):
    monkeypatch.setenv("LAB_EMERGENCY_MAX_TOKENS", "9000")
    client, fake = _client()

    response = client.complete(
        [{"role": "user", "content": "p"}],
        model="fake/model",
        max_tokens=12000,
    )

    assert fake.completions.kwargs["max_tokens"] == 9000
    assert response.requested_max_tokens == 9000
    assert response.max_tokens_source == "explicit+emergency"


def test_expected_token_ranges_are_observational_only():
    profile = {
        "agents": {
            "Theorist": {
                "model": "fake/model",
                "expected_tokens": {"min": 100, "max": 200},
            }
        }
    }

    assert expected_token_range("Theorist", "fake/model", profile=profile) == (100, 200)
    assert budget_snapshot("Theorist", "fake/model", 250, profile=profile) == {
        "expected_min": 100,
        "expected_max": 200,
        "actual": 250,
        "over_budget": True,
    }
    assert expected_token_range("Theorist", "other/model", profile=profile) == (None, None)


def test_trace_records_finish_reason_truncation_and_unusual_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lab.trace.budget_snapshot",
        lambda agent, model, total: {
            "expected_min": 10,
            "expected_max": 20,
            "actual": total,
            "over_budget": True,
        },
    )
    trace = Trace("budget", out_dir=tmp_path / "runs")
    trace.log(
        "agent_start",
        agent="Theorist",
        model="fake/model",
        reasoning_effort="high",
        step_key="iter:1:proposer",
    )
    response = LLMResponse(
        content="partial",
        model="fake/model",
        prompt_tokens=10,
        completion_tokens=15,
        latency_s=1.0,
        finish_reason="length",
        requested_max_tokens=9000,
        model_max_completion_tokens=12000,
        max_tokens_source="catalog+emergency",
        catalog_source="memory",
        reasoning_effort_sent="high",
        effort_resolution="exact",
    )

    trace.agent_call("Theorist", "fake/model", 0.2, [{"role": "user", "content": "p"}], response)
    trace.close()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    llm = next(event for event in events if event.get("type") == "llm_call")

    assert llm["finish_reason"] == "length"
    assert llm["truncated"] is True
    assert llm["requested_max_tokens"] == 9000
    assert llm["budget"]["over_budget"] is True
    assert any(event.get("type") == "unusually_expensive_call" for event in events)
    stage_end = next(event for event in events if event.get("type") == "stage_end")
    assert stage_end["truncated"] is True
    assert stage_end["budget"]["over_budget"] is True
