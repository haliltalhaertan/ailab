import json

from lab.client import LLMResponse
from lab.orchestrator import Orchestrator
from lab.trace import Trace
from lab.ui_live import build_cards, stage_timeline


class _FakeAgent:
    name = "Panelist"
    model = "fake/model"
    system_prompt = "system"
    temperature = 0.0
    reasoning_effort = None

    def respond(self, messages, stream_callback=None):
        if stream_callback is not None:
            stream_callback("content", "partial")
        response = LLMResponse(
            content="partial",
            model=self.model,
            prompt_tokens=10,
            completion_tokens=20,
            latency_s=0.25,
            finish_reason="length",
            requested_max_tokens=100,
        )
        return response.content, response


def test_generic_orchestrator_stage_carries_completion_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lab.orchestrator.budget_snapshot",
        lambda agent, model, total: {
            "expected_min": 5,
            "expected_max": 15,
            "actual": total,
            "over_budget": True,
        },
    )
    monkeypatch.setattr(
        "lab.trace.budget_snapshot",
        lambda agent, model, total: {
            "expected_min": 5,
            "expected_max": 15,
            "actual": total,
            "over_budget": True,
        },
    )
    trace = Trace("generic", out_dir=tmp_path / "runs")
    result = Orchestrator(trace).pipeline("task", [_FakeAgent()])
    trace.close()

    assert result == "partial"
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    stage_end = next(event for event in events if event.get("type") == "stage_end")
    assert stage_end["finish_reason"] == "length"
    assert stage_end["truncated"] is True
    assert stage_end["requested_max_tokens"] == 100
    assert stage_end["budget"]["over_budget"] is True

    card = build_cards(events)[0]
    assert card.truncated is True
    assert card.over_budget is True
    assert card.expected_max_tokens == 15

    row = stage_timeline(events)[0]
    assert row["truncated"] is True
    assert row["over_budget"] is True
