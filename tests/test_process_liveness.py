from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lab.integrity import ProjectBusyError, ProjectRunLock, process_alive


def _sleeping_child() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _stop_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=10)


def test_process_alive_tracks_external_child_and_exit():
    child = _sleeping_child()
    try:
        assert process_alive(child.pid) is True
        assert process_alive(os.getpid()) is True
        assert process_alive((2**22) + 12345) is False
    finally:
        _stop_child(child)

    assert child.poll() is not None
    assert process_alive(child.pid) is False


def test_live_external_process_lock_cannot_be_stolen(tmp_path: Path):
    child = _sleeping_child()
    root = tmp_path / "project"
    root.mkdir(parents=True)
    lock_path = root / "run.lock"
    lock_path.write_text(
        json.dumps(
            {
                "token": "external-owner",
                "pid": child.pid,
                "host": socket.gethostname(),
                "created_at_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )

    try:
        contender = ProjectRunLock(root)
        with pytest.raises(ProjectBusyError):
            contender.acquire()
        assert lock_path.exists()
    finally:
        _stop_child(child)


def test_dead_external_process_lock_is_reclaimed(tmp_path: Path):
    child = _sleeping_child()
    root = tmp_path / "project"
    root.mkdir(parents=True)
    lock_path = root / "run.lock"
    lock_path.write_text(
        json.dumps(
            {
                "token": "dead-owner",
                "pid": child.pid,
                "host": socket.gethostname(),
                "created_at_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )
    _stop_child(child)

    contender = ProjectRunLock(root)
    contender.acquire()
    try:
        assert contender.acquired is True
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["token"] == contender.token
    finally:
        contender.release()
