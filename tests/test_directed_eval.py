import json

import pytest

from lab.directed_eval import SUITE_ID, evaluate, main


def submission():
    return {"suite_id": SUITE_ID, "model": "fixture/no-model-called", "reasoning_effort": "low",
            "responses": [
                {"task_id": "sum-squares", "answer": {"value": 338350}},
                {"task_id": "modular-inverse", "answer": {"value": 38}},
                {"task_id": "polynomial-counterexample", "answer": {"n": 40, "d": 41}},
                {"task_id": "collatz-prefix", "answer": {"values": [22, 11, 34, 17, 52]}},
                {"task_id": "division-audit", "answer": {"analysis": "A plausible-looking proof is still unverified."}},
                {"task_id": "contraction-audit", "answer": {"analysis": "Arbitrary prose cannot receive EXACT_PASS."}},
            ]}


def test_correct_finite_answers_do_not_promote_prose_or_discovery():
    report = evaluate(submission())
    assert report["exact_passes"] == 4
    assert report["human_review_pending"] == 2
    assert not report["scientific_discovery_established"]
    assert not report["formal_kernel_verified"]
    assert report["usage"]["cost_usd"] is None
    assert not report["usage"]["cost_complete"]


@pytest.mark.parametrize("index,answer", [
    (0, {"value": 338351}), (0, {"value": 338350.0}),
    (1, {"value": 81}), (1, {"value": True}),
    (2, {"n": 0, "d": 41}), (2, {"n": 40, "d": 1}),
    (2, {"n": 101, "d": 2}),
    (3, {"values": [7, 22, 11, 34, 17]}),
])
def test_wrong_or_outside_domain_answers_fail(index, answer):
    data = submission()
    data["responses"][index]["answer"] = answer
    assert evaluate(data)["results"][index]["status"] == "FAIL"


def test_alternate_counterexample_is_checked_not_string_matched():
    data = submission()
    data["responses"][2]["answer"] = {"n": 41, "d": 41}
    assert evaluate(data)["exact_passes"] == 4


def test_missing_answers_remain_in_denominator():
    data = submission()
    data["responses"] = data["responses"][:1]
    result = evaluate(data)
    assert result["exact_pass_rate"] == 0.25
    assert result["results"][1]["status"] == "MISSING"


def test_duplicates_and_unknown_tasks_rejected():
    data = submission()
    data["responses"].append(data["responses"][0])
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(data)
    data["responses"] = [{"task_id": "unknown", "answer": {}}]
    with pytest.raises(ValueError, match="Unknown"):
        evaluate(data)


@pytest.mark.parametrize("usage", [
    {"cost_complete": True}, {"cost_usd": -1}, {"cost_usd": float("nan")},
    {"calls": True}, {"prompt_tokens": 1.5}, {"cost_complete": "true"},
])
def test_invalid_usage_rejected(usage):
    data = submission()
    data["usage"] = usage
    with pytest.raises(ValueError):
        evaluate(data)


def test_incomplete_cost_never_produces_cost_efficiency():
    data = submission()
    data["usage"] = {"cost_usd": 0.4, "cost_complete": False}
    assert evaluate(data)["cost_per_exact_pass_usd"] is None
    data["usage"]["cost_complete"] = True
    assert evaluate(data)["cost_per_exact_pass_usd"] == 0.1


def test_export_and_score_cli(tmp_path):
    exported, submitted, result = (tmp_path / name for name in ("tasks.json", "submission.json", "result.json"))
    assert main(["export", "--output", str(exported)]) == 0
    assert len(json.loads(exported.read_text(encoding="utf-8"))["tasks"]) == 6
    submitted.write_text(json.dumps(submission()), encoding="utf-8")
    assert main(["score", str(submitted), "--output", str(result)]) == 0
    assert json.loads(result.read_text(encoding="utf-8"))["exact_passes"] == 4
