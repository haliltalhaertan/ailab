"""Denetim bulguları — 2026-09-10 turu.

Bağımsız iki denetim (Hermes + Muse Code) tarafından üretilip prob ile doğrulanan
üç bulgunun regresyon koruması.

- F01: içeriği yazılamadan çöken ``run.lock`` projeyi kalıcı kilitliyordu.
- F02: ``read_json_tolerant`` geçersiz UTF-8 baytında ``UnicodeDecodeError`` ile
  patlıyordu; adının aksine tolerant değildi.
- F03: ``sorryAx`` derleyici çıktısı kapısından geçiyordu ve ortam değişkeniyle
  izin verilen axiom kümesine eklenebiliyordu.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lab.integrity import ProjectBusyError, ProjectRunLock, read_json_tolerant
from lab.tools import LeanTool


def _dead_pid() -> int:
    """Gerçek bir child process çalıştırıp ölü pid döndür (taşınabilir)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _write_lock(root: Path, payload: dict[str, object]) -> None:
    (root / "run.lock").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# F01 — sahiplik iddiası taşımayan run.lock reclaim edilebilmeli
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="empty-file"),
        pytest.param(b'{"token": "abc", "pi', id="truncated-json"),
        pytest.param(b"{}", id="empty-object"),
        pytest.param(b"   \n  ", id="whitespace-only"),
        pytest.param(b"\xff\xfe\x00garbage", id="invalid-utf8"),
    ],
)
def test_f01_ownerless_lock_is_reclaimed(tmp_path: Path, raw: bytes) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "run.lock").write_bytes(raw)

    lock = ProjectRunLock(root)
    lock.acquire()
    try:
        assert lock.acquired is True
        payload = json.loads((root / "run.lock").read_text(encoding="utf-8"))
        assert payload["token"] == lock.token
    finally:
        lock.release()


def test_f01_live_owner_is_never_reclaimed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_lock(
        root,
        {"token": "live", "pid": os.getpid(), "host": socket.gethostname(), "created_at_epoch": time.time()},
    )

    lock = ProjectRunLock(root)
    with pytest.raises(ProjectBusyError):
        lock.acquire()
    assert lock.acquired is False


def test_f01_foreign_host_is_never_reclaimed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_lock(
        root,
        {"token": "foreign", "pid": 123456, "host": "other-host.invalid", "created_at_epoch": time.time()},
    )

    lock = ProjectRunLock(root)
    with pytest.raises(ProjectBusyError):
        lock.acquire()
    assert lock.acquired is False


def test_f01_dead_pid_reclaim_still_works(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_lock(
        root,
        {"token": "dead", "pid": _dead_pid(), "host": socket.gethostname(), "created_at_epoch": time.time()},
    )

    lock = ProjectRunLock(root)
    lock.acquire()
    try:
        assert json.loads((root / "run.lock").read_text(encoding="utf-8"))["token"] == lock.token
    finally:
        lock.release()


def test_f01_ownerless_lock_keeps_mutual_exclusion(tmp_path: Path) -> None:
    """Sahipsiz kilidi reclaim etmek ikinci sahibe kapı açmamalı."""

    root = tmp_path / "project"
    root.mkdir()
    (root / "run.lock").write_bytes(b"")

    first = ProjectRunLock(root)
    first.acquire()
    try:
        second = ProjectRunLock(root)
        with pytest.raises(ProjectBusyError):
            second.acquire()
        assert second.acquired is False
    finally:
        first.release()


# --------------------------------------------------------------------------
# F02 — read_json_tolerant gerçekten tolerant olmalı
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\xff\xfe\x00garbage", id="invalid-utf8-bom"),
        pytest.param(b'{"status": "RUNNING", "t\xc3', id="truncated-mid-codepoint"),
        pytest.param(b"\x80\x81\x82", id="continuation-bytes-only"),
    ],
)
def test_f02_undecodable_json_returns_default(tmp_path: Path, raw: bytes) -> None:
    target = tmp_path / "runtime.json"
    target.write_bytes(raw)

    sentinel = {"status": "DEFAULT"}
    assert read_json_tolerant(target, sentinel) == sentinel


def test_f02_valid_json_still_parses(tmp_path: Path) -> None:
    target = tmp_path / "runtime.json"
    target.write_text(json.dumps({"status": "RUNNING", "başlık": "ç"}), encoding="utf-8")

    assert read_json_tolerant(target, {}) == {"status": "RUNNING", "başlık": "ç"}


# --------------------------------------------------------------------------
# F03 — sorryAx hiçbir yoldan PROVEN kapısını geçememeli
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "'sorryAx' does not depend on any axioms",
        "axioms: [propext, sorryAx]",
        "theorem foo depends on sorryAx",
    ],
)
def test_f03_sorry_ax_is_rejected_by_compiler_scan(text: str) -> None:
    assert LeanTool._compiler_mentions_unsafe_proof(text) is True


def test_f03_clean_compiler_output_is_still_accepted() -> None:
    assert LeanTool._compiler_mentions_unsafe_proof("build completed successfully") is False


def test_f03_sorry_ax_cannot_be_allowlisted_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ortam değişkeni sorryAx'i güvenilir axiom haline getirememeli."""

    monkeypatch.setenv("LAB_LEAN_ALLOWED_AXIOMS", "propext,Classical.choice,Quot.sound,sorryAx")
    assert "sorryax" in {x.casefold() for x in LeanTool.DENIED_AXIOMS}
    resolved = LeanTool._resolved_allowed_axioms()
    assert not {x.casefold() for x in resolved} & {x.casefold() for x in LeanTool.DENIED_AXIOMS}


def test_f03_default_allowlist_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAB_LEAN_ALLOWED_AXIOMS", raising=False)
    assert LeanTool._resolved_allowed_axioms() == {"propext", "Classical.choice", "Quot.sound"}
