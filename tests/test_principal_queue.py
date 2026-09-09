from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

import lab.principal_queue as module
from lab.principal_queue import PrincipalQueue
from test_directed import contract


def submit(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract()), encoding="utf-8")
    queue = PrincipalQueue(tmp_path / "queue")
    row = queue.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key="request-1")
    return queue, row, path


def test_idempotent_and_conflicting_submission(tmp_path):
    queue, row, path = submit(tmp_path)
    same = PrincipalQueue(queue.root).submit(path, input_root=tmp_path, parent_repo=tmp_path,
                                           idempotency_key="request-1")
    assert same["job_id"] == row["job_id"]
    data = json.loads(path.read_text())
    data["objective"] = "Different objective"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="different payload"):
        queue.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key="request-1")
    assert len(queue.list()) == 1


def test_invalid_never_enters_queue(tmp_path):
    queue = PrincipalQueue(tmp_path / "queue")
    path = tmp_path / "bad.json"
    path.write_text('{}')
    with pytest.raises(ValueError):
        queue.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key="bad")
    assert queue.list() == []


def test_concurrent_submission_shares_one_job(tmp_path):
    queue, row, path = submit(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: PrincipalQueue(queue.root).submit(
            path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key="request-1"), range(8)))
    assert {value["job_id"] for value in rows} == {row["job_id"]}
    assert len(queue.list()) == 1


def test_atomic_dispatch_once_immutable_submission(tmp_path, monkeypatch):
    queue, row, path = submit(tmp_path)
    expected = path.read_bytes()
    path.write_text("changed original")
    calls = []
    monkeypatch.setattr(module, "preflight_directed_task", lambda *a, **k: {"gate_status": "FEASIBLE"})

    def run(path, **kwargs):
        assert path.read_bytes() == expected
        assert kwargs["background"] is True
        calls.append(1)
        folder = tmp_path / "run"
        folder.mkdir()
        (folder / "runtime.json").write_text(json.dumps({"run_id": "run-one", "status": "RUNNING"}))
        return SimpleNamespace(run_id="run-one", status="QUEUED", artifact_root=str(folder))

    monkeypatch.setattr(module, "run_directed_task", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: PrincipalQueue(queue.root).dispatch(), range(2)))
    assert len(calls) == 1
    assert sum(value is None for value in results) == 1
    status = queue.status(row["job_id"])
    assert status["state"] == "DISPATCHED"
    assert status["run_status"] == "RUNNING"
    with pytest.raises(ValueError, match="Only a QUEUED"):
        queue.dispatch(row["job_id"])


def test_preflight_block_requires_explicit_retry(tmp_path, monkeypatch):
    queue, row, _ = submit(tmp_path)
    monkeypatch.setattr(module, "preflight_directed_task", lambda *a, **k: {"gate_status": "INPUT_INTEGRITY_FAILURE"})
    monkeypatch.setattr(module, "run_directed_task", lambda *a, **k: pytest.fail("Must not launch"))
    assert queue.dispatch()["state"] == "BLOCKED_PREFLIGHT"
    assert queue.dispatch() is None
    assert queue.retry_preflight(row["job_id"])["state"] == "QUEUED"


def test_uncertain_launch_survives_restart_no_retry(tmp_path, monkeypatch):
    queue, row, _ = submit(tmp_path)
    monkeypatch.setattr(module, "preflight_directed_task", lambda *a, **k: {"gate_status": "FEASIBLE"})

    def lost_response(*args, **kwargs):
        raise RuntimeError("Response lost after worker may have started")

    monkeypatch.setattr(module, "run_directed_task", lost_response)
    assert queue.dispatch()["state"] == "DISPATCHING"
    restarted = PrincipalQueue(queue.root)
    assert restarted.dispatch() is None
    assert restarted.status(row["job_id"])["detail"]["ambiguous_launch"]
    with pytest.raises(ValueError, match="uncertain launches"):
        restarted.retry_preflight(row["job_id"])


def test_status_rejects_other_runtime_binding(tmp_path, monkeypatch):
    queue, row, _ = submit(tmp_path)
    folder = tmp_path / "foreign"
    folder.mkdir()
    (folder / "runtime.json").write_text('{"run_id":"other","status":"COMPLETED"}')
    queue._update(row["job_id"], "DISPATCHED", {}, run_id="expected", artifact_root=str(folder))
    status = queue.status(row["job_id"])
    assert status["run_status"] is None
    assert "binding" in status["runtime_warning"]


def test_process_exit_during_dispatch_cannot_be_reclaimed(tmp_path, monkeypatch):
    queue, row, _ = submit(tmp_path)

    def exit_process(*args, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(module, "preflight_directed_task", exit_process)
    with pytest.raises(SystemExit):
        queue.dispatch()
    restarted = PrincipalQueue(queue.root)
    assert restarted.status(row["job_id"])["state"] == "DISPATCHING"
    assert restarted.dispatch() is None
