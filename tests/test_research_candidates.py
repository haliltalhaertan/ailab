import json

import pytest

from lab.directed_eval import SUITE_ID
from lab.research_candidates import evaluate_candidates, main


def candidate(identifier="a", **kwargs):
    return {"candidate_id": identifier, "task_id": "modular-inverse", "answer": {"value": 38},
            "model": "offline-fixture", **kwargs}


def archive(*items):
    return evaluate_candidates({"suite_id": SUITE_ID, "candidates": list(items)})


def test_duplicate_evaluation_preserves_provenance_and_order():
    a = candidate("a", usage={"cost_usd": 0.1, "cost_complete": True})
    b = candidate("b", parent_id="a", model="different-model")
    result = archive(b, a)
    assert result == archive(a, b)
    assert result["unique_evaluations"] == 1
    assert result["rankings_by_task"]["modular-inverse"] == ["a"]
    assert result["candidates"][1]["duplicate_of"] == "a"
    assert result["candidates"][1]["provenance"]["model"] == "different-model"
    assert result["candidates"][1]["provenance"]["usage"]["cost_usd"] is None


def test_exact_fail_and_unverified_prose_are_not_selected():
    result = archive(candidate(answer={"value": 1}),
                     candidate("b", task_id="division-audit", answer={"analysis": "Clearly 2=1"}))
    assert [row["status"] for row in result["candidates"]] == ["FAIL", "REVIEW_REQUIRED"]
    assert result["candidates"][1]["exact_score"] is None
    assert not any(result["rankings_by_task"].values())
    assert result["formal_kernel_verified"] is False
    assert result["model_calls"] == 0


@pytest.mark.parametrize("items", [
    [candidate(parent_id="missing")],
    [candidate(parent_id="a")],
    [candidate(parent_id="b"), candidate("b", parent_id="a")],
    [candidate(parent_id="b"), candidate("b", task_id="sum-squares")],
    [candidate(), candidate()],
    [candidate(parent_id=[])],
    [candidate(task_id="arbitrary-python")],
    [candidate(answer="__import__('os')")],
    [candidate(usage=[])],
    [candidate(), candidate("b", usage={"cost_usd": -1})],
    [candidate(), candidate("b", usage={"cost_complete": True})],
    [candidate(answer={"value": float("nan")})],
])
def test_rejects_bad_lineage_data_and_duplicate_provenance(items):
    with pytest.raises(ValueError):
        archive(*items)


def test_bounded_input():
    with pytest.raises(ValueError):
        archive(*(candidate(str(i)) for i in range(257)))
    with pytest.raises(ValueError):
        archive(candidate(answer={"analysis": "x" * 2_000_000}))


def test_cli_is_deterministic_and_does_not_execute_answer(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "archive.json"
    source.write_text(json.dumps({"suite_id": SUITE_ID, "candidates": [candidate(
        task_id="division-audit", answer={"analysis": "raise SystemExit('never execute')"})]}))
    assert main([str(source), "--output", str(output)]) == 0
    first = output.read_bytes()
    assert main([str(source), "--output", str(output)]) == 0
    assert output.read_bytes() == first
    assert json.loads(first)["candidates"][0]["status"] == "REVIEW_REQUIRED"
