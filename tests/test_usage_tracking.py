import json
from types import SimpleNamespace

from lab.openrouter_catalog import parse_models_payload
from lab.trace import Trace


def test_openrouter_catalog_parses_pricing_per_million():
    models = parse_models_payload(
        {
            "data": [
                {
                    "id": "vendor/model-x",
                    "name": "Model X",
                    "context_length": 123456,
                    "pricing": {
                        "prompt": "0.00000125",
                        "completion": "0.00001",
                    },
                }
            ]
        }
    )
    assert len(models) == 1
    assert models[0].id == "vendor/model-x"
    assert models[0].prompt_usd_per_million == 1.25
    assert models[0].completion_usd_per_million == 10.0
    assert "vendor/model-x" in models[0].label


def test_trace_summary_records_model_tokens_cost_and_wall_time(tmp_path):
    trace = Trace("usage", out_dir=tmp_path)
    response = SimpleNamespace(
        content="answer",
        prompt_tokens=120,
        completion_tokens=30,
        reasoning_tokens=10,
        cached_tokens=20,
        cost_usd=0.0042,
        latency_s=1.25,
    )
    trace.agent_call(
        "Theorist",
        "vendor/model-x",
        0.2,
        [{"role": "user", "content": "problem"}],
        response,
    )
    summary_path = trace.close()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["total_calls"] == 1
    assert summary["total_prompt_tokens"] == 120
    assert summary["total_completion_tokens"] == 30
    assert summary["total_reasoning_tokens"] == 10
    assert summary["total_cached_tokens"] == 20
    assert summary["total_tokens"] == 150
    assert summary["total_cost_usd"] == 0.0042
    assert summary["cost_complete"] is True
    assert summary["wall_time_s"] >= 0

    agent = summary["agents"]["Theorist"]
    assert agent["models"] == ["vendor/model-x"]
    assert agent["total_tokens"] == 150
    assert agent["cost_usd"] == 0.0042


def test_trace_marks_partial_cost_when_provider_omits_cost(tmp_path):
    trace = Trace("partial", out_dir=tmp_path)
    response = SimpleNamespace(
        content="answer",
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=0,
        cached_tokens=0,
        cost_usd=None,
        latency_s=0.1,
    )
    trace.agent_call("A", "model", 0.0, [], response)
    summary = json.loads(trace.close().read_text(encoding="utf-8"))
    assert summary["total_cost_usd"] == 0.0
    assert summary["cost_complete"] is False
