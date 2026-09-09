import json

import pytest

from lab.directed_reports import build_research_report, render_research_report


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def setup_run(tmp_path, lanes, results, usage=None):
    put(tmp_path / "TASK_CONTRACT.json", {"task_id": "test", "agent_plan": {"lanes": lanes}})
    put(tmp_path / "runtime.json", {"status": "PARTIAL", "usage": {"lanes": usage or {}}})
    for name, result in results.items():
        put(tmp_path / "lanes" / name / "RESULT.json", result)
    return tmp_path


def model(name, **kwargs):
    return {"lane_id": name, "model": "test/model", "role": "Check an inequality", "reasoning_effort": "low", **kwargs}


def test_empty_answer_not_success_and_native_not_in_model_denominator(tmp_path):
    root = setup_run(tmp_path, [model("a"), model("b"), model("c", execution="symbolic_algebra")], {
        "a": {"status": "COMPLETED", "public_findings": "  "},
        "b": {"status": "COMPLETED", "public_findings": "A candidate identity", "claim_results": [{"status": "PROVED"}]},
        "c": {"status": "COMPLETED", "public_findings": "exact check"},
    })
    report = build_research_report(root)
    metrics = report["model_metrics"][0]
    assert metrics["completed_answers"] == 1
    assert metrics["attempted_lanes"] == 2
    assert metrics["answer_completion_rate"] == 0.5
    assert metrics["useful_count"] is None
    assert metrics["review_status"] == "NOT_REVIEWED"
    assert not metrics["cost_complete"]
    assert metrics["upper_bound_usd"] is None


def test_timeout_cost_unknown_despite_other_completed_lane(tmp_path):
    root = setup_run(tmp_path, [model("a"), model("b"), model("blocked")], {
        "a": {"status": "COMPLETED", "public_findings": "answer", "usage": {"wall_seconds": 4}},
        "b": {"status": "TIMEOUT", "usage": {"wall_seconds": 300}},
        "blocked": {"status": "BLOCKED_DEPENDENCY"},
    }, {
        "a": {"calls": 1, "provider_cost_usd": 0.06, "provider_cost_complete": True},
        "b": {"calls": 1, "provider_cost_usd": 0, "provider_cost_complete": False, "provider_cost_upper_bound_usd": 0.07},
    })
    metrics = build_research_report(root)["model_metrics"][0]
    assert metrics["scheduled_lanes"] == 3
    assert metrics["attempted_lanes"] == 2
    assert metrics["known_cost_usd"] == 0.06
    assert metrics["cost_complete"] is False
    assert metrics["upper_bound_usd"] == 0.13
    assert metrics["wall_seconds_sum"] == 304


def test_retry_last_usage_is_not_exact_total(tmp_path):
    root = setup_run(tmp_path, [model("a")], {
        "a": {"status": "COMPLETED", "public_findings": "answer", "usage": {"cost_usd": 0.02}, "provider_attempts": [{}, {}]},
    })
    metrics = build_research_report(root)["model_metrics"][0]
    assert metrics["known_cost_usd"] == 0.02
    assert not metrics["cost_complete"]
    assert metrics["upper_bound_usd"] is None


def test_no_file_mutation_or_traversal_and_efforts_grouped(tmp_path):
    root = setup_run(tmp_path, [model("a"), model("b", reasoning_effort="high"), model("../../outside")], {})
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = build_research_report(root)
    assert len(report["lanes"]) == 2
    assert len(report["model_metrics"]) == 2
    assert all(m["answer_completion_rate"] is None for m in report["model_metrics"])
    assert "NOT_REVIEWED" in render_research_report(report)
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_malformed_active_snapshot_is_tolerated(tmp_path):
    put(tmp_path / "TASK_CONTRACT.json", {"agent_plan": {"lanes": [model("a")]}})
    (tmp_path / "runtime.json").write_text('{"status":', encoding="utf-8")
    assert build_research_report(tmp_path)["lanes"][0]["status"] == "PENDING"


def test_invalid_cost_not_treated_as_known_free(tmp_path):
    root = setup_run(tmp_path, [model("a")], {
        "a": {"status": "COMPLETED", "public_findings": "answer", "usage": {"cost_usd": True}},
    })
    metrics = build_research_report(root)["model_metrics"][0]
    assert not metrics["cost_complete"]
    assert metrics["upper_bound_usd"] is None


@pytest.mark.parametrize("answer", [None, {}, [], 123, True])
def test_nontext_answers_never_count_as_completed(tmp_path, answer):
    root = setup_run(tmp_path, [model("a")], {"a": {"status": "COMPLETED", "public_findings": answer}})
    assert build_research_report(root)["model_metrics"][0]["completed_answers"] == 0
