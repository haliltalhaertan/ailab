from types import SimpleNamespace

from lab.client import LLMClient, _RequestPolicy


class _FakeCompletions:
    def create(self, **kwargs):
        assert kwargs["stream"] is True
        return [
            SimpleNamespace(
                model="fake/model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content="",
                            reasoning="r1",
                            reasoning_details=[{"type": "detail", "data": "d1"}],
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                model="fake/model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        delta=SimpleNamespace(
                            content="answer",
                            reasoning="r2",
                            reasoning_details=[{"type": "detail", "data": "d2"}],
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                model="fake/model",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=2),
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
                    cost=0.001,
                ),
                choices=[],
            ),
        ]


class _FakeOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_reasoning_details_are_not_duplicated_into_stream_callback():
    client = LLMClient.__new__(LLMClient)
    client._client = _FakeOpenAI()
    seen = []
    policy = _RequestPolicy(
        requested_max_tokens=1234,
        model_max_completion_tokens=2000,
        max_tokens_source="catalog+emergency",
        catalog_source="memory",
        reasoning_effort_requested="high",
        reasoning_effort_sent="high",
        effort_resolution="exact",
        reasoning_max_tokens_sent=None,
    )

    response = client._complete_stream(
        {"model": "fake/model", "messages": [{"role": "user", "content": "p"}]},
        [{"role": "user", "content": "p"}],
        "fake/model",
        policy,
        lambda channel, delta: seen.append((channel, delta)),
    )

    assert [channel for channel, _ in seen] == ["reasoning", "content", "reasoning"]
    assert response.provider_reasoning == "r1r2"
    assert response.content == "answer"
    assert response.reasoning_details == [
        {"type": "detail", "data": "d1"},
        {"type": "detail", "data": "d2"},
    ]
    assert response.finish_reason == "length"
    assert response.requested_max_tokens == 1234
    assert response.model_max_completion_tokens == 2000
