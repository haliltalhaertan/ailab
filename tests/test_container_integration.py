from __future__ import annotations

import shutil
import subprocess

import pytest

from lab.code_experiment import GuardedExperimentWorkspace


def _docker_or_skip() -> str:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker is not available")
    probe = subprocess.run(
        [docker, "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
    )
    if probe.returncode != 0:
        pytest.skip("Docker daemon is not available")
    return docker


def _workspace(tmp_path, monkeypatch, *, timeout_s: int = 5) -> GuardedExperimentWorkspace:
    docker = _docker_or_skip()
    monkeypatch.setenv("LAB_CODE_EXTRA_IMPORTS", "urllib,pathlib")
    return GuardedExperimentWorkspace(
        tmp_path / "workspace",
        timeout_s=timeout_s,
        container_engine=docker,
        container_image="python:3.12-slim",
    )


def test_container_has_no_network(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    source = """import urllib.request
try:
    urllib.request.urlopen("http://1.1.1.1", timeout=1)
except OSError:
    print("network-blocked")
else:
    raise SystemExit(7)
"""
    assert ws.write_file("network.py", source).ok
    result = ws.run_python("network.py")
    assert result.ok, result.error
    assert "network-blocked" in result.output


def test_container_rootfs_is_read_only(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    source = """import pathlib
try:
    pathlib.Path("/etc/hostname").write_text("ailab-write-probe")
except OSError:
    print("rootfs-read-only")
else:
    raise SystemExit(8)
"""
    assert ws.write_file("readonly.py", source).ok
    result = ws.run_python("readonly.py")
    assert result.ok, result.error
    assert "rootfs-read-only" in result.output


def test_container_outputs_directory_is_writable(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    source = """import pathlib
path = pathlib.Path("/workspace/outputs/container-probe.txt")
path.write_text("ok")
print(path.read_text())
"""
    assert ws.write_file("output.py", source).ok
    result = ws.run_python("output.py")
    assert result.ok, result.error
    assert result.output.strip() == "ok"
    assert (ws.outputs / "container-probe.txt").read_text(encoding="utf-8") == "ok"


def test_container_timeout_removes_container(tmp_path, monkeypatch):
    docker = _docker_or_skip()
    ws = _workspace(tmp_path, monkeypatch, timeout_s=2)
    assert ws.write_file("spin.py", "while True:\n    pass\n").ok
    result = ws.run_python("spin.py")
    assert not result.ok
    assert result.metadata["termination_reason"] == "timeout"

    listed = subprocess.run(
        [docker, "ps", "--format", "{{.Names}}"],
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    names = {line.strip() for line in listed.stdout.splitlines() if line.strip()}
    assert not any(name.startswith("ailab-exp-") for name in names)


def test_container_imports_canonical_definitions_file(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    definitions = """def sigma(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps
"""
    source = """from definitions import sigma
print(sigma(60))
"""
    assert ws.write_file("definitions.py", definitions).ok
    assert ws.write_file("exp_001.py", source).ok
    result = ws.run_python("exp_001.py")
    assert result.ok, result.error
    assert result.output.strip() == "19"
