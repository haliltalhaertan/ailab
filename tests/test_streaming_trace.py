import json
from types import SimpleNamespace

from lab.agent import Agent
from lab.trace import Trace


class FakeStreamingClient:
    def complete(self, messages, model=None, temperature=0.7, max_tokens=None, stream_callback=None):
        assert stream_callback is not None
        stream_callback("reasoning", "first thought ")
        stream_callback("reasoning", "second thought")
        stream_callback("content", '{"answer":"ok"}')
        return SimpleNamespace(
            content='{"answer":"ok"}',
            model=model or "fake/model",
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=3,
            cached_tokens=0,
            cost_usd=0.001,
            latency_s=0.2,
            provider_reasoning="first thought second thought",
            reasoning_details=None,
            request_messages=messages,
        )


def test_agent_streams_provider_deltas_into_active_trace(tmp_path):
    trace = Trace("stream", out_dir=tmp_path)
    agent = Agent(
        name="Theorist",
        system_prompt="think",
        model="fake/model",
        client=FakeStreamingClient(),
    )

    content, response = agent.respond([{"role": "user", "content": "problem"}])
    trace.agent_call(agent.name, response.model, agent.temperature, [], response)
    trace.close()

    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    stream_events = [event for event in events if event.get("type") == "agent_stream"]
    assert content == '{"answer":"ok"}'
    assert [event["channel"] for event in stream_events] == ["reasoning", "reasoning", "content"]
    assert stream_events[0]["delta"] == "first thought "
    assert stream_events[-1]["delta"] == '{"answer":"ok"}'
    final = [event for event in events if event.get("type") == "llm_call"][0]
    assert final["provider_reasoning"] == "first thought second thought"
