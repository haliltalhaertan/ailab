import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from lab.research_review import build_reviewed_report, load_reviews, record_review


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "run"
    store = tmp_path / "feedback" / "reviews.json"
    put(root / "TASK_CONTRACT.json", {"agent_plan": {"lanes": [
        {"lane_id": lane, "model": "test/model", "reasoning_effort": "low"} for lane in ["a", "b", "c"]]}})
    for lane in ["a", "b", "c"]:
        put(root / "lanes" / lane / "RESULT.json", {"status": "COMPLETED", "public_findings": "candidate", "claim_results": [{"status": "OPEN"}]})
    put(root / "runtime.json", {"usage": {"lanes": {lane: {"calls": 1, "provider_cost_usd": 0.1, "provider_cost_complete": True} for lane in ["a", "b", "c"]}}})
    return root, store


def review(data, lane="a", decision="useful", **kwargs):
    root, store = data
    return record_review(store, root, lane, decision, kwargs.get("note", "Checked finite cases"), "principal", kwargs.get("minutes", 2.0))


def test_append_only_and_frozen_bytes_unchanged(data):
    root, store = data
    before = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    first = review(data)
    review(data, decision="needs_work")
    events = json.loads(store.read_text())["events"]
    assert len(events) == 2 and events[0] == first
    assert first["result_sha256"] == hashlib.sha256(before[str(root / "lanes/a/RESULT.json")]).hexdigest()
    assert first["reviewer_authenticated"] is False
    assert load_reviews(store, root)["a"]["decision"] == "needs_work"
    assert before == {str(p): p.read_bytes() for p in root.rglob("*.json")}


@pytest.mark.parametrize("changed", ["TASK_CONTRACT.json", "lanes/a/RESULT.json"])
def test_changed_source_stales_feedback_and_rereview_restores(data, changed):
    root, store = data
    review(data)
    path = root / changed
    path.write_bytes(path.read_bytes() + b"\n")
    assert load_reviews(store, root)["a"]["stale"]
    assert build_reviewed_report(root, store)["model_metrics"][0]["reviewed_count"] == 0
    review(data, decision="not_useful")
    assert load_reviews(store, root)["a"]["current"]


def test_review_denominator_and_group_cost(data):
    root, store = data
    review(data)
    review(data, "b", "needs_work", minutes=3)
    metrics = build_reviewed_report(root, store)["model_metrics"][0]
    assert metrics["reviewed_count"] == 2
    assert metrics["useful_count"] == 1 and metrics["useful_rate"] == .5
    assert metrics["review_minutes"] == 5
    assert metrics["cost_per_useful_usd"] == pytest.approx(.3)
    runtime = json.loads((root / "runtime.json").read_text())
    runtime["usage"]["lanes"]["c"]["provider_cost_complete"] = False
    put(root / "runtime.json", runtime)
    assert build_reviewed_report(root, store)["model_metrics"][0]["cost_per_useful_usd"] is None


def test_concurrent_threads_keep_all_events(data):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: review(data), range(24)))
    events = json.loads(data[1].read_text())["events"]
    assert len(events) == len({e["event_id"] for e in events}) == 24


def test_cross_process_lock_refuses_overwrite(data):
    root, store = data
    review(data)
    before = store.read_bytes()
    lock = store.with_name(store.name + ".lock")
    lock.write_text("other process")
    with pytest.raises(ValueError, match="writer"):
        review(data)
    assert store.read_bytes() == before
    assert lock.exists()


def test_corrupt_store_never_overwritten(data):
    root, store = data
    store.parent.mkdir()
    store.write_text('{"events": []}')
    before = store.read_bytes()
    with pytest.raises(ValueError):
        review(data)
    assert store.read_bytes() == before


@pytest.mark.parametrize("minutes", [-1, float("nan"), float("inf"), True])
def test_invalid_duration(data, minutes):
    with pytest.raises(ValueError):
        review(data, minutes=minutes)


def test_reject_undeclared_and_inside_run_store(data):
    root, store = data
    with pytest.raises(ValueError):
        review(data, "../a")
    with pytest.raises(ValueError):
        review(data, "unknown")
    with pytest.raises(ValueError):
        record_review(root / "reviews.json", root, "a", "useful", "", "principal", 1)
    assert not store.exists()


def test_missing_result_stales_existing_review(data):
    root, store = data
    review(data)
    (root / "lanes/a/RESULT.json").unlink()
    assert load_reviews(store, root)["a"]["stale"]


def test_review_not_shared_by_another_run(data, tmp_path):
    root, store = data
    review(data)
    assert load_reviews(store, tmp_path / "another-run") == {}
