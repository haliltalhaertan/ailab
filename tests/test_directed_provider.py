"""Provider streaming adapter tests use only an in-process OpenAI stand-in."""
import json
from types import SimpleNamespace

import pytest

from lab.directed_provider import DirectedProvider, ProviderStopped


def event(*, content=None, finish=None, extra=None, **fields):
    delta = SimpleNamespace(content=content, model_extra=extra or {}, **fields)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


@pytest.fixture
def transport(monkeypatch):
    import lab.directed_provider as provider

    state = {"events": [], "clients": [], "requests": [], "closed": 0}

    class Stream:
        def __enter__(self):
            return iter(state["events"])

        def __exit__(self, *args):
            state["closed"] += 1

    def create(**kwargs):
        state["requests"].append(kwargs)
        return Stream()

    class Client:
        def __init__(self, **kwargs):
            state["clients"].append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            state["closed"] += 1

    monkeypatch.setattr(provider, "OpenAI", Client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-syntheticcredentialvalue")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    return state


def lane():
    return SimpleNamespace(model="fake/model", max_completion_tokens=500, reasoning_effort="medium")


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("in_extra", [False, True])
def test_provider_reasoning_aliases_and_structured_details(transport, field, in_extra):
    class Detail:
        def model_dump(self):
            return {"type": "reasoning.text", "text": "Public provider fragment", "index": 0}

    detail = Detail()
    transport["events"] = [
        event(extra={field: "Visible explanation", "reasoning_details": [detail]}) if in_extra else
        event(**{field: "Visible explanation", "reasoning_details": [detail]}),
        event(content='{"findings":"result"}', finish="stop"),
    ]
    callbacks = []
    result = DirectedProvider().call(lane(), "task", lambda channel, value: callbacks.append((channel, value)),
                                     lambda: False, 20, "stable-step")
    assert result["provider_reasoning"] == "Visible explanation"
    assert result["reasoning_details"] == [detail.model_dump()]
    assert ("reasoning_details", [detail.model_dump()]) in callbacks
    assert result["complete"] is True
    assert transport["clients"][0]["max_retries"] == 0
    assert transport["requests"][0]["extra_headers"] == {"Idempotency-Key": "stable-step"}
    assert transport["requests"][0]["max_tokens"] == 500
    assert transport["closed"] == 2


def test_provider_does_not_invent_reasoning_and_redacts_return_and_callbacks(transport):
    transport["events"] = [event(content="Authorization: Bearer sk-syntheticcredentialvalue\nresult",
                                 extra={"reasoning_details": [{"type": "reasoning.text",
                                        "text": "sk-syntheticcredentialvalue", "index": 7}]}, finish="stop")]
    callbacks = []
    result = DirectedProvider().call(lane(), "task", lambda channel, value: callbacks.append((channel, value)),
                                     lambda: False, 20, "step")
    assert result["provider_reasoning"] == ""
    assert result["reasoning_details"][0]["index"] == 7
    assert "syntheticcredentialvalue" not in json.dumps([result, callbacks])
    assert "[REDACTED]" in json.dumps([result, callbacks])


def test_provider_stream_cancellation_closes_client_and_preserves_delivered_callback(transport):
    transport["events"] = [event(extra={"reasoning_details": [{"text": "first"}]}),
                           event(content="must not deliver", finish="stop")]
    callbacks = []

    def callback(channel, value):
        callbacks.append((channel, value))

    with pytest.raises(ProviderStopped):
        DirectedProvider().call(lane(), "task", callback, lambda: bool(callbacks), 20, "step")
    assert callbacks == [("reasoning_details", [{"text": "first"}])]
    assert transport["closed"] == 2
    assert len(transport["requests"]) == 1


def test_provider_cancelled_before_dispatch_creates_no_client(transport):
    with pytest.raises(ProviderStopped):
        DirectedProvider().call(lane(), "task", lambda *_: None, lambda: True, 20, "step")
    assert transport["clients"] == []
