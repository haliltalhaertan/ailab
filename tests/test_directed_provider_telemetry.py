"""No-network regression checks for early directed-call telemetry."""
from types import SimpleNamespace

import pytest

from lab.directed_provider import DirectedProvider, ProviderStopped
from test_directed_provider import event, lane, transport as _transport


@pytest.fixture
def transport(monkeypatch):
    return _transport.__wrapped__(monkeypatch)


def test_first_generation_metadata_precedes_cancellation_and_excludes_headers(transport):
    chunk = event(content="must not deliver")
    chunk.id = "gen-known"
    chunk.model = "resolved/model"
    chunk.model_extra = {"provider": "Provider", "headers": {"Authorization": "secret"}, "api_key": "secret"}
    transport["events"] = [chunk]
    callbacks = []
    with pytest.raises(ProviderStopped):
        DirectedProvider().call(lane(), "task", lambda *args: callbacks.append(args),
                                lambda: bool(callbacks), 20, "step")
    assert callbacks == [("metadata", {"id": "gen-known", "model": "resolved/model", "provider": "Provider"})]


def test_usage_and_id_survive_later_stream_failure(transport):
    chunk = event()
    chunk.id = "gen-partial"
    chunk.usage = SimpleNamespace(prompt_tokens=12, completion_tokens=30, cost=0.03)

    def events():
        yield chunk
        raise TimeoutError("transport timeout")

    transport["events"] = events()
    callbacks = []
    with pytest.raises(TimeoutError):
        DirectedProvider().call(lane(), "task", lambda *args: callbacks.append(args), lambda: False, 20, "step")
    assert callbacks[0] == ("metadata", {"id": "gen-partial"})
    assert callbacks[1][0] == "usage"
    assert callbacks[1][1]["cost_usd"] == 0.03


def test_metadata_returned_and_duplicate_chunks_do_not_repeat_callback(transport):
    chunk = event(finish="stop")
    chunk.id = "gen-final"
    transport["events"] = [chunk, chunk]
    callbacks = []
    result = DirectedProvider().call(lane(), "task", lambda *args: callbacks.append(args), lambda: False, 20, "step")
    assert result["metadata"] == {"id": "gen-final"}
    assert callbacks == [("metadata", {"id": "gen-final"})]


@pytest.mark.parametrize("base", ["https://openrouter.ai/api/v1", "https://api.openai.com/v1"])
def test_none_effort_is_explicit(transport, monkeypatch, base):
    monkeypatch.setenv("OPENAI_BASE_URL", base)
    request_lane = lane()
    request_lane.reasoning_effort = "none"
    DirectedProvider().call(request_lane, "task", lambda *_: None, lambda: False, 20, "step")
    request = transport["requests"][0]
    assert (request["extra_body"]["reasoning"]["effort"] if "openrouter.ai" in base else request["reasoning_effort"]) == "none"


def test_numeric_cap_excludes_effort(transport):
    request_lane = lane()
    request_lane.max_reasoning_tokens = 100
    DirectedProvider().call(request_lane, "task", lambda *_: None, lambda: False, 20, "step")
    assert transport["requests"][0]["extra_body"] == {"reasoning": {"max_tokens": 100}}


def test_numeric_cap_unsupported_adapter_fails_before_dispatch(transport, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    request_lane = lane()
    request_lane.max_reasoning_tokens = 100
    with pytest.raises(ValueError, match="OpenRouter"):
        DirectedProvider().call(request_lane, "task", lambda *_: None, lambda: False, 20, "step")
    assert transport["clients"] == []


def test_metadata_and_usage_arrive_before_wall_time_guard(transport, monkeypatch):
    import lab.directed_provider as provider

    chunk = event(content="too late")
    chunk.id = "gen-time-limit"
    chunk.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, cost=0.001)
    transport["events"] = [chunk]
    times = iter([0.0, 21.0])
    monkeypatch.setattr(provider.time, "monotonic", lambda: next(times))
    callbacks = []
    with pytest.raises(TimeoutError, match="wall-time"):
        DirectedProvider().call(lane(), "task", lambda *args: callbacks.append(args), lambda: False, 20, "step")
    assert [channel for channel, _ in callbacks] == ["metadata", "usage"]
    assert callbacks[0][1]["id"] == "gen-time-limit"
