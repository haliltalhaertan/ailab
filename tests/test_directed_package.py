import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

from lab.directed_package import build_package, findings, verify_package


def payload(folder):
    ledger = {
        "task_id": "partial-1",
        "claims": [
            {"claim_id": "C1", "statement": "Undecided", "status": "OPEN", "first_missing_step": "Unavailable tool"}
        ],
        "downstream_claims_not_established": ["XUB"],
    }
    contract = b'{"task_id":"partial-1"}'
    (folder / "TASK_CONTRACT.json").write_bytes(contract)
    (folder / "TASK_CONTRACT_SHA256.txt").write_text(hashlib.sha256(contract).hexdigest())
    (folder / "CLAIM_LEDGER.json").write_text(json.dumps(ledger), encoding="utf-8")
    (folder / "MASTER_FINDINGS.md").write_bytes(findings(ledger).encode("utf-8"))
    return ledger


def pin_zip(folder):
    digest = hashlib.sha256((folder / "COMPLETE_PACKAGE.zip").read_bytes()).hexdigest()
    (folder / "PACKAGE_SHA256.txt").write_bytes((digest + "  COMPLETE_PACKAGE.zip\n").encode())


def test_partial_package_deterministic_and_standalone(tmp_path):
    payload(tmp_path)
    archive, digest = build_package(tmp_path)
    first = archive.read_bytes()
    assert verify_package(tmp_path)["ok"]
    assert build_package(tmp_path)[1] == digest
    assert archive.read_bytes() == first
    result = subprocess.run([sys.executable, str(tmp_path / "VERIFY.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(archive) as z:
        assert z.namelist() == sorted(z.namelist())
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())
        assert "PACKAGE_SHA256.txt" not in z.namelist()
        assert "VERIFY_OUTPUT.txt" in z.namelist()


def test_modified_source_rejected(tmp_path):
    payload(tmp_path)
    build_package(tmp_path)
    (tmp_path / "CLAIM_LEDGER.json").write_text("{}")
    with pytest.raises(ValueError, match="source hash"):
        verify_package(tmp_path)


@pytest.mark.parametrize("name", ["EXTRA.txt", "nested/FINAL_SHA256SUMS.txt"])
def test_extra_source_rejected(tmp_path, name):
    payload(tmp_path)
    build_package(tmp_path)
    p = tmp_path / name
    p.parent.mkdir(exist_ok=True)
    p.write_text("extra")
    with pytest.raises(ValueError, match="membership"):
        verify_package(tmp_path)


@pytest.mark.parametrize("mode", ["extra", "changed", "duplicate"])
def test_corrupt_zip_even_with_updated_external_hash(tmp_path, mode):
    payload(tmp_path)
    archive, _ = build_package(tmp_path)
    with zipfile.ZipFile(archive) as z:
        entries = [(i.filename, z.read(i)) for i in z.infolist()]
    if mode == "extra":
        entries.append(("extra.txt", b"bad"))
    elif mode == "changed":
        entries = [(n, b"bad" if n == "CLAIM_LEDGER.json" else b) for n, b in entries]
    else:
        entries.append(entries[0])
    with zipfile.ZipFile(archive, "w") as z:
        for n, b in entries:
            z.writestr(n, b)
    pin_zip(tmp_path)
    with pytest.raises(ValueError, match="ZIP"):
        verify_package(tmp_path)


def test_markdown_ledger_contradiction_rejected_on_build(tmp_path):
    payload(tmp_path)
    (tmp_path / "MASTER_FINDINGS.md").write_text("Everything is PROVED")
    with pytest.raises(ValueError, match="contradiction"):
        build_package(tmp_path)


@pytest.mark.parametrize(
    "name,content",
    [
        (".env", "ordinary"),
        ("credentials.json", "{}"),
        ("artifact.txt", "Bearer real-test-credential"),
        ("artifact.txt", "api_key=abcdefghijk"),
        ("artifact.json", '{"api_key": "abcdefghijk"}'),
        ("artifact.txt", "-----BEGIN PRIVATE KEY-----\nabc"),
    ],
)
def test_secrets_rejected(tmp_path, name, content):
    payload(tmp_path)
    (tmp_path / name).write_text(content)
    with pytest.raises(ValueError):
        build_package(tmp_path)


def test_fixed_receipt_not_just_hash_checked(tmp_path):
    payload(tmp_path)
    build_package(tmp_path)
    receipt = tmp_path / "VERIFY_OUTPUT.txt"
    receipt.write_text("PASS: all scientific claims PROVED")
    manifest = tmp_path / "FINAL_SHA256SUMS.txt"
    lines = manifest.read_text().splitlines()
    lines = [
        hashlib.sha256(receipt.read_bytes()).hexdigest() + "  VERIFY_OUTPUT.txt"
        if line.endswith("  VERIFY_OUTPUT.txt")
        else line
        for line in lines
    ]
    manifest.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="receipt"):
        verify_package(tmp_path)


def test_untrusted_verifier_not_executed(tmp_path):
    payload(tmp_path)
    build_package(tmp_path)
    (tmp_path / "VERIFY.py").write_text('raise RuntimeError("EXECUTED")')
    with pytest.raises(ValueError, match="Untrusted"):
        verify_package(tmp_path)


def test_missing_zip_rejected(tmp_path):
    payload(tmp_path)
    archive, _ = build_package(tmp_path)
    archive.unlink()
    with pytest.raises(ValueError, match="missing ZIP"):
        verify_package(tmp_path)


def consolidated_payload(folder):
    ledger = payload(folder)
    ledger.update(schema_version="1.1", authoritative=True)
    ledger["claims"][0]["status"] = "INCONCLUSIVE"
    ledger["claims"][0]["supporting_evidence"] = [
        {
            "lane_id": "b",
            "status": "REFUTED",
            "evidence_files": ["LANES/b/EVIDENCE.json"],
            "independence_level": "SHARED_CODE_PATH",
        },
        {
            "lane_id": "a",
            "status": "PROVED",
            "evidence_files": ["LANES/a/EVIDENCE.json"],
            "independence_level": "SHARED_CODE_PATH",
        },
    ]
    (folder / "CLAIM_LEDGER.json").write_text(json.dumps(ledger), encoding="utf-8")
    (folder / "MASTER_FINDINGS.md").write_bytes(findings(ledger).encode("utf-8"))
    return ledger


def test_consolidated_single_table_and_deterministic_package(tmp_path):
    ledger = consolidated_payload(tmp_path)
    markdown = findings(ledger)
    assert markdown.count("| C1 | INCONCLUSIVE |") == 1
    assert "## C1" not in markdown
    assert markdown.index("Lane a:") < markdown.index("Lane b:")
    assert "Agreement is not a proof certificate" in markdown
    archive, digest = build_package(tmp_path)
    assert build_package(tmp_path)[1] == digest
    assert verify_package(tmp_path)["ok"]
    result = subprocess.run([sys.executable, str(tmp_path / "VERIFY.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_consolidated_duplicate_claim_ids_rejected(tmp_path):
    ledger = consolidated_payload(tmp_path)
    ledger["claims"].append(dict(ledger["claims"][0]))
    (tmp_path / "CLAIM_LEDGER.json").write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate authoritative"):
        build_package(tmp_path)


def test_consolidated_markdown_contradiction_rejected(tmp_path):
    consolidated_payload(tmp_path)
    path = tmp_path / "MASTER_FINDINGS.md"
    path.write_text(path.read_text(encoding="utf-8").replace("| INCONCLUSIVE |", "| PROVED |"), encoding="utf-8")
    with pytest.raises(ValueError, match="contradiction"):
        build_package(tmp_path)


def test_exact_legacy_verifier_allowlisted(tmp_path, monkeypatch):
    import lab.directed_package as package

    payload(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(package, "VERIFY_SOURCE", package.LEGACY_VERIFY_SOURCE)
        build_package(tmp_path)
    assert (tmp_path / "VERIFY.py").read_bytes() == package.LEGACY_VERIFY_SOURCE.encode("utf-8")
    assert package.verify_package(tmp_path)["ok"]
    original = (tmp_path / "COMPLETE_PACKAGE.zip").read_bytes()
    original_digest = hashlib.sha256(original).hexdigest()
    assert package.build_package(tmp_path)[1] == original_digest
    assert (tmp_path / "COMPLETE_PACKAGE.zip").read_bytes() == original
    assert (tmp_path / "VERIFY.py").read_bytes() == package.LEGACY_VERIFY_SOURCE.encode("utf-8")


def test_new_ledger_cannot_select_legacy_verifier(tmp_path, monkeypatch):
    import lab.directed_package as package

    consolidated_payload(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(package, "VERIFY_SOURCE", package.LEGACY_VERIFY_SOURCE)
        with pytest.raises(ValueError, match="Legacy verifier"):
            build_package(tmp_path)
