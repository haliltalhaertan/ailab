import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab.directed import code_inventory, verify_code_inventory, run_directed_task, resume_directed_task
from lab.directed_gate import safe_bytes, sha, verify_snapshot
from test_directed import COMMIT, FakeProvider, contract


@pytest.fixture
def setup_task(tmp_path, monkeypatch):
    import lab.directed_gate as gate

    monkeypatch.setattr(gate, "parent_commit", lambda p: COMMIT)

    def setup():
        path = tmp_path / "contract.json"
        path.write_text(json.dumps(contract()), encoding="utf-8")
        return path

    return setup, tmp_path / "state"


@pytest.mark.parametrize(
    "content",
    [
        {"api_key": "abcdefghijk"},
        {"client_secret": "abcdefghijk"},
        {"password": "abcdefghijk"},
        {"access_token": "abcdefghijk"},
    ],
)
def test_json_credentials_blocked_before_input_use(content):
    assert not safe_bytes(json.dumps(content).encode())


def test_private_key_input_rejected():
    assert not safe_bytes(b"-----BEGIN PRIVATE KEY-----\nabc")


@pytest.mark.parametrize("mutation", ["delete", "alter", "extra", "optional_added"])
def test_snapshot_actual_bytes_and_membership(tmp_path, mutation):
    source = tmp_path / "input.txt"
    source.write_bytes(b"original")
    specs = [
        SimpleNamespace(path="input.txt", sha256=sha(b"original"), required=True),
        SimpleNamespace(path="optional.txt", sha256=sha(b"optional"), required=False),
    ]
    c = SimpleNamespace(inputs=specs, input_manifests=[])
    expected = [{"path": "input.txt", "status": "PASS"}, {"path": "optional.txt", "status": "OPTIONAL_MISSING"}]
    verify_snapshot(c, tmp_path, expected)
    if mutation == "delete":
        source.unlink()
    elif mutation == "alter":
        source.write_bytes(b"changed")
    else:
        (tmp_path / ("extra.txt" if mutation == "extra" else "optional.txt")).write_bytes(b"optional")
    with pytest.raises(ValueError, match="snapshot"):
        verify_snapshot(c, tmp_path, expected)


@pytest.mark.parametrize("mutation", ["alter_helper", "delete_helper", "add_helper"])
def test_execution_inventory_includes_helpers_and_membership(tmp_path, mutation):
    (tmp_path / "directed.py").write_text("# coordinator")
    helper = tmp_path / "claim_check.py"
    helper.write_text("# exact checker v1")
    expected = code_inventory(tmp_path)
    assert "claim_check.py" in expected
    verify_code_inventory(expected, tmp_path)
    if mutation == "alter_helper":
        helper.write_text("# different checker")
    elif mutation == "delete_helper":
        helper.unlink()
    else:
        (tmp_path / "extra.py").write_text("# extra helper")
    with pytest.raises(ValueError, match="inventory"):
        verify_code_inventory(expected, tmp_path)


def test_finalization_failure_can_resume_without_repeat_calls(setup_task, monkeypatch):
    import lab.directed as directed

    setup, root = setup_task
    provider = FakeProvider()
    real_build = directed.build_package

    def fail_build(package):
        raise RuntimeError("Simulated interruption before archive")

    monkeypatch.setattr(directed, "build_package", fail_build)
    with pytest.raises(RuntimeError, match="Simulated"):
        run_directed_task(setup(), provider=provider, root=root)
    folder = next((root / "test-project" / "directed").iterdir())
    runtime = json.loads((folder / "runtime.json").read_text())
    assert runtime["status"] == "PAUSED_ERROR"
    assert runtime["interrupted_phase"] == "FINALIZING"
    assert "Simulated" in runtime["last_error"]
    calls = list(provider.calls)
    monkeypatch.setattr(directed, "build_package", real_build)
    result = resume_directed_task("test-project", folder.name, provider=provider, root=root)
    assert result.status == "COMPLETED_WITH_OPEN_CLAIMS"
    assert Path(result.package_path).is_file()
    assert provider.calls == calls


def test_terminal_resume_does_not_trust_missing_or_altered_archive(setup_task):
    setup, root = setup_task
    provider = FakeProvider()
    result = run_directed_task(setup(), provider=provider, root=root)
    Path(result.package_path).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="ZIP"):
        resume_directed_task("test-project", result.run_id, provider=provider, root=root)
    assert len(provider.calls) == 4


def test_terminal_resume_rechecks_frozen_contract(setup_task):
    setup, root = setup_task
    provider = FakeProvider()
    result = run_directed_task(setup(), provider=provider, root=root)
    frozen = Path(result.artifact_root) / "TASK_CONTRACT.json"
    data = json.loads(frozen.read_text())
    data["objective"] = "Different task after completion"
    frozen.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen contract"):
        resume_directed_task("test-project", result.run_id, provider=provider, root=root)
    assert len(provider.calls) == 4


def test_queued_stop_before_worker_runtime_prevents_dispatch(setup_task, monkeypatch):
    import lab.directed as directed

    setup, root = setup_task
    monkeypatch.setattr(directed, "_launch", lambda *args, **kwargs: 123)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-testing-credential")
    result = run_directed_task(setup(), background=True, root=root)
    assert not (root / "test-project" / "runtime.json").exists()
    directed.stop_directed_task("test-project", result.run_id, root=root)
    assert (Path(result.artifact_root) / "stop.flag").is_file()
    provider = FakeProvider()
    provider.identity = "openai-compatible-v1"
    stopped = directed._execute("test-project", result.run_id, provider=provider, root=root)
    assert stopped.status == "STOPPED"
    assert not provider.calls


def test_promised_output_missing_fails_closed(setup_task):
    setup, root = setup_task
    path = setup()
    value = json.loads(path.read_text())
    value["required_outputs"].append("PROMISED_REPORT.md")
    path.write_text(json.dumps(value), encoding="utf-8")
    provider = FakeProvider()
    with pytest.raises(ValueError, match="Required delivery outputs missing: PROMISED_REPORT.md"):
        run_directed_task(path, provider=provider, root=root)
    folder = next((root / "test-project" / "directed").iterdir())
    assert json.loads((folder / "runtime.json").read_text())["status"] == "PAUSED_ERROR"
    assert not (folder / "result.json").exists()


@pytest.mark.parametrize("mutation", ["coordinated_rewrite", "capability_only", "missing_seal"])
def test_frozen_context_signature_prevents_coordinated_rewrite(setup_task, monkeypatch, mutation):
    import lab.directed as directed

    setup, root = setup_task
    monkeypatch.setattr(directed, "_launch", lambda *args, **kwargs: 123)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-testing-credential")
    result = run_directed_task(setup(), background=True, root=root)
    folder = Path(result.artifact_root)
    provider = FakeProvider()
    provider.identity = "openai-compatible-v1"
    if mutation == "missing_seal":
        (folder / "context.seal.json").unlink()
    else:
        capability = json.loads((folder / "CAPABILITY_REPORT.json").read_text())
        capability["estimated_call_ceiling"] = 100
        if mutation == "coordinated_rewrite":
            frozen = folder / "TASK_CONTRACT.json"
            changed = json.loads(frozen.read_text())
            changed["objective"] = "Unauthorized different task"
            frozen.write_text(json.dumps(changed), encoding="utf-8")
            capability["contract_hash"] = sha(frozen.read_bytes())
        (folder / "CAPABILITY_REPORT.json").write_text(json.dumps(capability), encoding="utf-8")
        if mutation == "coordinated_rewrite":
            context_path = folder / "context.json"
            context = json.loads(context_path.read_text())
            context["contract_hash"] = sha((folder / "TASK_CONTRACT.json").read_bytes())
            context["capability_report_hash"] = sha((folder / "CAPABILITY_REPORT.json").read_bytes())
            context_path.write_text(json.dumps(context), encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen (context seal|capability report)"):
        directed._execute("test-project", result.run_id, provider=provider, root=root)
    assert not provider.calls
