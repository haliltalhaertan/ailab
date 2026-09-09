"""Deterministic, self-verifying delivery packages. No runtime keys or arbitrary files."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
import zipfile

from lab.directed_gate import safe_bytes
from lab.directed_contract import safe_relative

RECEIPT = "PASS: source, ledger, contract and ZIP verified\n"
EXCLUDED = {"COMPLETE_PACKAGE.zip", "FINAL_SHA256SUMS.txt", "PACKAGE_SHA256.txt"}
SECRET_ASSIGNMENT = r"""(?i)(?:api[_-]?key|password|client_secret|access_token|refresh_token|private_key)["']?\s*[=:]\s*["']?[^\s"',}]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"""


def _safe_content(raw: bytes) -> bool:
    return safe_bytes(raw) and re.search(SECRET_ASSIGNMENT, raw.decode("utf-8", errors="replace")) is None


def _legacy_findings(ledger: dict) -> str:
    lines = ["# Directed task findings", "", "Task: " + ledger["task_id"], ""]
    for claim in ledger["claims"]:
        lines += [
            f"## {claim['claim_id']} — {claim['status']}",
            "",
            claim["statement"],
            "",
            "Domain: " + claim.get("domain", ""),
            "",
            "First missing step: " + str(claim.get("first_missing_step") or "None"),
            "",
        ]
    lines += [
        "Downstream claims not established: " + ", ".join(ledger.get("downstream_claims_not_established", [])),
        "",
    ]
    return "\n".join(lines)


VERIFY_SOURCE = """import hashlib, json, pathlib, re, sys, zipfile
def h(b): return hashlib.sha256(b).hexdigest()
def findings(ledger):
    lines = ['# Directed task findings', '', 'Task: ' + ledger['task_id'], '']
    for claim in ledger['claims']:
        lines += [f"## {claim['claim_id']} — {claim['status']}", '', claim['statement'], '', 'Domain: ' + claim.get('domain', ''), '', 'First missing step: ' + str(claim.get('first_missing_step') or 'None'), '']
    lines += ['Downstream claims not established: ' + ', '.join(ledger.get('downstream_claims_not_established', [])), '']
    return '\\n'.join(lines)
def verify(root):
    root = pathlib.Path(root)
    excluded = {'FINAL_SHA256SUMS.txt', 'COMPLETE_PACKAGE.zip', 'PACKAGE_SHA256.txt'}
    for p in root.rglob('*'):
        if p.is_symlink(): raise ValueError('symlink rejected')
        name = p.relative_to(root).as_posix()
        if any(part.startswith('.') for part in pathlib.PurePosixPath(name).parts) or ':' in name or '\\\\' in name: raise ValueError('unsafe member')
        if any(re.search(r'(?i)(^|[_.-])(env|secret|credentials|authorization|token|private.?key)([_.-]|$)', part) for part in pathlib.PurePosixPath(name).parts): raise ValueError('secret filename')
    listed = {}
    for line in (root/'FINAL_SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        if not re.fullmatch('[0-9a-f]{64}', digest) or name in listed or name in excluded or '..' in pathlib.PurePosixPath(name).parts or pathlib.PurePosixPath(name).is_absolute() or ':' in name or '\\\\' in name: raise ValueError('unsafe/duplicate manifest member')
        listed[name] = digest
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in excluded}
    if actual != set(listed): raise ValueError('manifest membership mismatch')
    for name, digest in listed.items():
        p = root/name
        if p.is_symlink() or h(p.read_bytes()) != digest: raise ValueError('source hash mismatch: '+name)
    if (root/'VERIFY_OUTPUT.txt').read_bytes() != b'PASS: source, ledger, contract and ZIP verified\\n': raise ValueError('invalid verification receipt')
    ledger = json.loads((root/'CLAIM_LEDGER.json').read_text(encoding='utf-8'))
    if (root/'MASTER_FINDINGS.md').read_text(encoding='utf-8') != findings(ledger): raise ValueError('claim ledger/Markdown contradiction')
    if (root/'TASK_CONTRACT_SHA256.txt').read_text().strip() != h((root/'TASK_CONTRACT.json').read_bytes()): raise ValueError('contract hash mismatch')
    archive = root/'COMPLETE_PACKAGE.zip'
    if not archive.is_file(): raise ValueError('missing ZIP')
    if (root/'PACKAGE_SHA256.txt').read_text(encoding='utf-8') != h(archive.read_bytes()) + '  COMPLETE_PACKAGE.zip\\n': raise ValueError('ZIP hash mismatch')
    if archive.exists():
        with zipfile.ZipFile(archive) as z:
            expected = actual | {'FINAL_SHA256SUMS.txt'}
            if len(z.namelist()) != len(set(z.namelist())) or set(z.namelist()) != expected: raise ValueError('ZIP membership mismatch')
            for name in expected:
                if z.read(name) != (root/name).read_bytes(): raise ValueError('ZIP/source mismatch: '+name)
    return True
if __name__ == '__main__':
    try:
        verify(pathlib.Path(__file__).parent)
        print('PASS: source, ledger, contract and ZIP verified')
    except Exception as e:
        print('FAIL: '+str(e)); sys.exit(1)
"""

# A portable verifier also rejects credential-shaped content; the host additionally
# checks current process credentials via safe_bytes before packaging or verification.
VERIFY_SOURCE = VERIFY_SOURCE.replace(
    "    listed = {}",
    '    for p in root.rglob("*"):\n'
    "        if p.is_file() and p.relative_to(root).as_posix() not in excluded:\n"
    '            text = p.read_bytes().decode("utf-8", errors="replace")\n'
    f'            if re.search({SECRET_ASSIGNMENT!r}, text): raise ValueError("secret content")\n'
    "    listed = {}",
)

# Exact previously released trusted verifier. Keep this construction unchanged so
# historical packages remain verifiable without running package-supplied code.
LEGACY_VERIFY_SOURCE = VERIFY_SOURCE
CONSOLIDATED_FINDINGS_SOURCE = r"""
def _cell(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', '').replace('\n', '<br>')

def findings(ledger):
    version = ledger.get('schema_version', '1.0')
    if version == '1.0' and ledger.get('authoritative') is not True:
        return legacy_findings(ledger)
    if version != '1.1' or ledger.get('authoritative') is not True:
        raise ValueError('Unsupported or non-authoritative consolidated ledger')
    claims = ledger['claims']
    ids = [claim['claim_id'] for claim in claims]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate authoritative claim id')
    claims = sorted(claims, key=lambda claim: claim['claim_id'])
    lines = ['# Directed task findings', '', 'Task: ' + _cell(ledger['task_id']), '',
             '| Claim | Status | Statement |', '| --- | --- | --- |']
    for claim in claims:
        lines.append('| ' + ' | '.join(_cell(claim[key]) for key in ('claim_id', 'status', 'statement')) + ' |')
    lines += ['', '## Supporting evidence', '',
              'Shared implementations and shared inputs limit independence. Agreement is not a proof certificate; conflicting evidence requires review, never a vote.', '']
    for claim in claims:
        lines += ['- **' + _cell(claim['claim_id']) + '**: domain ' + _cell(claim.get('domain', '')) +
                  '; first missing step: ' + _cell(claim.get('first_missing_step') or 'None') + '.']
        evidence = claim.get('supporting_evidence', [])
        if not isinstance(evidence, list):
            raise ValueError('Supporting evidence must be a list')
        for item in sorted(evidence, key=lambda value: (value['lane_id'], value.get('status', ''))):
            files = item.get('evidence_files', [])
            if not isinstance(files, list):
                raise ValueError('Evidence files must be a list')
            lines += ['  - Lane ' + _cell(item['lane_id']) + ': ' + _cell(item['status']) +
                      '; independence: ' + _cell(item.get('independence_level', 'NOT_INDEPENDENT')) +
                      '; files: ' + ', '.join(_cell(path) for path in sorted(files)) + '.']
        if not evidence:
            lines += ['  - No supporting lane evidence recorded.']
    lines += ['', 'Downstream claims not established: ' + ', '.join(_cell(item) for item in ledger.get('downstream_claims_not_established', [])), '']
    return '\n'.join(lines)
"""
VERIFY_SOURCE = LEGACY_VERIFY_SOURCE.replace("def findings(ledger):", "def legacy_findings(ledger):").replace(
    "def verify(root):", CONSOLIDATED_FINDINGS_SOURCE + "\ndef verify(root):", 1
)
_renderer_namespace: dict[str, Any] = {"legacy_findings": _legacy_findings}
exec(compile(CONSOLIDATED_FINDINGS_SOURCE, "<trusted-findings-renderer>", "exec"), _renderer_namespace)


def findings(ledger: dict) -> str:
    return _renderer_namespace["findings"](ledger)


def build_package(folder: Path) -> tuple[Path, str]:
    folder = Path(folder)
    # Reject symlinks before writing generated names, including existing receipts.
    for path in [folder, *folder.rglob("*")]:
        if path.is_symlink():
            raise ValueError("Unsafe package symlink")
    verifier_path = folder / "VERIFY.py"
    selected_source = VERIFY_SOURCE
    if verifier_path.is_file() and verifier_path.read_bytes() == LEGACY_VERIFY_SOURCE.encode("utf-8"):
        ledger = json.loads((folder / "CLAIM_LEDGER.json").read_text(encoding="utf-8"))
        if ledger.get("schema_version", "1.0") == "1.0" and ledger.get("authoritative") is not True:
            # Preserve genuine historical bytes/digest. Never silently repair an
            # altered archive while presenting it as the original delivery.
            verify_package(folder)
            selected_source = LEGACY_VERIFY_SOURCE
    verifier_path.write_bytes(selected_source.encode("utf-8"))
    (folder / "VERIFY_OUTPUT.txt").write_bytes(RECEIPT.encode("utf-8"))
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.relative_to(folder).as_posix() not in EXCLUDED)
    lines = []
    for path in files:
        name = path.relative_to(folder).as_posix()
        if safe_relative(name) != name or not _safe_content(path.read_bytes()):
            raise ValueError("Unsafe package member")
        lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(folder).as_posix())
    (folder / "FINAL_SHA256SUMS.txt").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    # Manifest cannot hash itself. ZIP cannot contain its own hash. The external
    # PACKAGE_SHA256.txt hashes ZIP; manifest covers sources including fixed receipt.
    # These hashes detect corruption, not authenticity; pin the returned ZIP digest.
    archive = folder / "COMPLETE_PACKAGE.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted([*files, folder / "FINAL_SHA256SUMS.txt"]):
            info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, path.read_bytes())
    (folder / "PACKAGE_SHA256.txt").write_bytes(
        (hashlib.sha256(archive.read_bytes()).hexdigest() + "  COMPLETE_PACKAGE.zip\n").encode("utf-8")
    )
    verify_package(folder)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def verify_package(folder: Path) -> dict:
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError("Unsafe package symlink")
    supplied_verifier = (folder / "VERIFY.py").read_bytes()
    trusted_versions = {source.encode("utf-8"): source for source in (VERIFY_SOURCE, LEGACY_VERIFY_SOURCE)}
    if supplied_verifier not in trusted_versions:
        raise ValueError("Untrusted package verifier")
    trusted_source = trusted_versions[supplied_verifier]
    ledger = json.loads((folder / "CLAIM_LEDGER.json").read_text(encoding="utf-8"))
    if trusted_source == LEGACY_VERIFY_SOURCE and ledger.get("schema_version", "1.0") != "1.0":
        raise ValueError("Legacy verifier cannot validate a consolidated ledger")
    for path in folder.rglob("*"):
        name = path.relative_to(folder).as_posix()
        if path.is_symlink() or safe_relative(name) != name:
            raise ValueError("Unsafe package member")
        if path.is_file() and name not in EXCLUDED and not _safe_content(path.read_bytes()):
            raise ValueError("Secret content in package")
    # Execute our checked-in verifier definition, never an untrusted package's VERIFY.py.
    namespace: dict[str, Any] = {"__name__": "verifier"}
    exec(compile(trusted_source, "<checked-in-directed-verifier>", "exec"), namespace)
    namespace["verify"](folder)
    findings(ledger)  # Apply version/uniqueness checks after source integrity succeeds.
    return {"ok": True, "package_sha256": hashlib.sha256((folder / "COMPLETE_PACKAGE.zip").read_bytes()).hexdigest()}
