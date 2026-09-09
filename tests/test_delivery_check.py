import hashlib
import json

import pytest

from lab.delivery_check import build_delivery_check


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture_run(tmp_path):
    write(tmp_path, "TASK_CONTRACT.json", {"task_id": "test", "agent_plan": {"lanes": [{"lane_id": "one"}]},
                                           "required_outputs": ["MASTER_FINDINGS.md"]})
    write(tmp_path, "runtime.json", {"status": "COMPLETED_WITH_OPEN_CLAIMS"})
    write(tmp_path, "lanes/one/RESULT.json", {"status": "COMPLETED", "public_findings": "Identity delivered",
                                             "claim_results": [{"status": "OPEN"}], "first_missing_step": "Uniform bound open"})
    (tmp_path / "package").mkdir()
    (tmp_path / "package/MASTER_FINDINGS.md").write_text("Findings", encoding="utf-8")
    return tmp_path


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_open_claim_complete_delivery_and_no_mutation(tmp_path):
    root = fixture_run(tmp_path)
    before = hashes(root)
    report = build_delivery_check(root)
    assert report["delivery_status"] == "COMPLETE"
    assert report["lanes"][0]["declared_claim_statuses"] == ["OPEN"]
    assert report["scientific_validation"] == "NOT_PERFORMED"
    assert report["human_review"] == "NOT_ASSESSED"
    assert hashes(root) == before


def test_reasoning_only_does_not_count_as_answer(tmp_path):
    root = fixture_run(tmp_path)
    write(root, "lanes/one/RESULT.json", {"status": "COMPLETED"})
    write(root, "lanes/one/RESPONSE.json", {"reasoning": "Many tokens", "content": " "})
    assert build_delivery_check(root)["delivery_status"] == "PARTIAL"


def test_response_fallback_requires_completed_result(tmp_path):
    root = fixture_run(tmp_path)
    write(root, "lanes/one/RESULT.json", {"status": "COMPLETED"})
    write(root, "lanes/one/RESPONSE.json", {"content": "Final answer"})
    assert build_delivery_check(root)["delivery_status"] == "COMPLETE"
    write(root, "lanes/one/RESULT.json", {"status": "TIMEOUT"})
    assert build_delivery_check(root)["delivery_status"] == "PARTIAL"


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/secret", "a\\b", ".env", "nested/../../secret"])
def test_unsafe_output_paths_fail_closed(tmp_path, name):
    root = fixture_run(tmp_path)
    write(root, "TASK_CONTRACT.json", {"agent_plan": {"lanes": [{"lane_id": "one"}]}, "required_outputs": [name]})
    report = build_delivery_check(root)
    assert report["delivery_status"] == "PARTIAL"
    assert report["required_outputs"][0]["status"] == "UNSAFE"


def test_missing_and_empty_outputs(tmp_path):
    root = fixture_run(tmp_path)
    path = root / "package/MASTER_FINDINGS.md"
    path.write_text("")
    assert build_delivery_check(root)["required_outputs"][0]["status"] == "EMPTY"
    path.unlink()
    assert build_delivery_check(root)["required_outputs"][0]["status"] == "MISSING"


def test_running_waits_for_result_and_package(tmp_path):
    root = fixture_run(tmp_path)
    (root / "lanes/one/RESULT.json").unlink()
    write(root, "runtime.json", {"status": "RUNNING", "lanes": {"one": "RUNNING"}})
    assert build_delivery_check(root)["delivery_status"] == "PENDING"
    write(root, "runtime.json", {"status": "STOPPED"})
    assert build_delivery_check(root)["delivery_status"] == "PARTIAL"


def test_malformed_contract_is_graceful(tmp_path):
    (tmp_path / "TASK_CONTRACT.json").write_text("{")
    report = build_delivery_check(tmp_path)
    assert report["delivery_status"] == "PARTIAL"
    assert report["warnings"]


def test_duplicate_lanes_rejected(tmp_path):
    root = fixture_run(tmp_path)
    write(root, "TASK_CONTRACT.json", {"agent_plan": {"lanes": [{"lane_id": "one"}, {"lane_id": "one"}]},
                                       "required_outputs": ["MASTER_FINDINGS.md"]})
    assert build_delivery_check(root)["delivery_status"] == "PARTIAL"


def test_symlink_package_escape(tmp_path):
    root = fixture_run(tmp_path / "run")
    outside = tmp_path / "outside.txt"
    outside.write_text("Must not count")
    target = root / "package/MASTER_FINDINGS.md"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink unavailable on this host")
    report = build_delivery_check(root)
    assert report["delivery_status"] == "PARTIAL"
    assert report["required_outputs"][0]["status"] == "UNSAFE"
