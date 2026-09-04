from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from lab.integrity import content_fingerprint, sha256_file


FORMAL_VERIFICATION_RE = re.compile(
    r"\b(?:Lean[- ]verified|formally verified|Lean[- ]doğrulanmış)\b",
    re.IGNORECASE,
)
VERIFICATION_RE = re.compile(r"\b(?:verified|doğrulanmış)\b", re.IGNORECASE)
PROVEN_RE = re.compile(r"\b(?:proven|ispatlandı|kanıtlandı)\b", re.IGNORECASE)
FORMAL_METADATA_KEYS = {
    "item_id",
    "claim_sha256",
    "lean_file",
    "file",
    "lean_sha256",
    "theorem_name",
    "theorem_type",
    "iteration",
    "axioms",
    "formal_verified",
    "formal_binding_verified",
    "axioms_verified",
    "source_clean",
    "proof_seal",
    "evidence_key_mode",
    "sealed_at",
}
_ACTIVE_REVISION_BINDING: ContextVar[dict[str, Any]] = ContextVar(
    "ailab_active_revision_binding",
    default={},
)


def claim_hash(claim: str) -> str:
    return content_fingerprint("claim:v1", str(claim or ""))


def set_active_revision_binding(item_id: str, claim: str, revision: int) -> dict[str, Any]:
    binding = {
        "item_id": str(item_id),
        "claim_hash": claim_hash(claim),
        "revision": int(revision),
    }
    _ACTIVE_REVISION_BINDING.set(binding)
    return binding


def active_revision_binding() -> dict[str, Any]:
    return dict(_ACTIVE_REVISION_BINDING.get())


def _evidence_kind(metadata: dict[str, Any] | None) -> str:
    raw = (metadata or {}).get("evidence")
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("kind") or "").upper()


def verification_claim_annotation(
    title: str,
    claim: str,
    *,
    status: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Classify self-verification language without changing canonical text."""

    text = f"{title}\n{claim}"
    terms: list[str] = []
    requirements: list[str] = []

    if FORMAL_VERIFICATION_RE.search(text):
        terms.append("formal_verified")
        requirements.append("FORMAL_PROOF")
    stripped = FORMAL_VERIFICATION_RE.sub("", text)
    if VERIFICATION_RE.search(stripped):
        terms.append("verified")
        requirements.append("EXACT_OR_EXHAUSTIVE_OR_FORMAL")
    if PROVEN_RE.search(text):
        terms.append("proven")
        requirements.append("PROVEN_FORMAL")

    if not terms:
        return {
            "self_verification_claim": False,
            "verification_claim_supported": True,
            "verification_claim_terms": [],
            "verification_claim_requirements": [],
        }

    kind = _evidence_kind(metadata)
    status_upper = str(status or "OPEN").upper()
    supported = True
    for requirement in requirements:
        if requirement == "FORMAL_PROOF":
            supported = supported and kind == "FORMAL_PROOF"
        elif requirement == "EXACT_OR_EXHAUSTIVE_OR_FORMAL":
            supported = supported and (
                kind == "FORMAL_PROOF"
                or kind == "EXACT_PASS"
                or kind.startswith("EXHAUSTIVE_")
            )
        elif requirement == "PROVEN_FORMAL":
            supported = supported and kind == "FORMAL_PROOF" and status_upper == "PROVEN"

    return {
        "self_verification_claim": True,
        "verification_claim_supported": bool(supported),
        "verification_claim_terms": terms,
        "verification_claim_requirements": requirements,
    }


def revision_record(
    *,
    revision: int,
    title: str,
    claim: str,
    status: str,
    created_at: str,
    iteration: int | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    raw_evidence = meta.get("evidence")
    evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else None
    formal_metadata = {key: meta[key] for key in FORMAL_METADATA_KEYS if key in meta}
    return {
        "revision": int(revision),
        "title": str(title),
        "claim": str(claim),
        "claim_hash": claim_hash(claim),
        "status": str(status),
        "created_at": str(created_at),
        "iteration": int(iteration) if iteration is not None else None,
        "target_id": str(meta.get("target_id") or ""),
        "target_hash": str(meta.get("target_hash") or ""),
        "claim_role": str(meta.get("claim_role") or ""),
        "evidence": evidence,
        "formal_metadata": formal_metadata,
        "strategy": meta.get("strategy"),
        "evidence_needed": list(meta.get("evidence_needed") or []),
        "tool_request_summary": dict(meta.get("tool_request_summary") or {}),
        "self_verification_claim": bool(meta.get("self_verification_claim")),
        "verification_claim_supported": bool(meta.get("verification_claim_supported", True)),
    }


def sync_current_revision(
    revisions: list[dict[str, Any]],
    *,
    current_revision: int,
    title: str,
    claim: str,
    status: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    output = [dict(entry) for entry in revisions]
    for index, entry in enumerate(output):
        if int(entry.get("revision", 0) or 0) != int(current_revision):
            continue
        updated = dict(entry)
        updated.update(
            revision_record(
                revision=current_revision,
                title=title,
                claim=claim,
                status=status,
                created_at=str(entry.get("created_at") or ""),
                iteration=entry.get("iteration"),
                metadata=metadata,
            )
        )
        output[index] = updated
        return output
    return output


def clear_revision_bound_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Drop evidence/proof fields that may not cross a claim revision boundary."""

    blocked = {
        "evidence",
        "status_guard",
        *FORMAL_METADATA_KEYS,
        "claim_hash",
    }
    return {key: value for key, value in metadata.items() if key not in blocked}


def _immutable_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if path.exists():
        if sha256_file(path) == digest:
            return path
        path = path.with_name(f"{path.stem}-{digest[:8]}{path.suffix}")
        if path.exists():
            if sha256_file(path) != digest:
                raise ValueError(f"immutable artifact collision: {path}")
            return path
    path.write_bytes(encoded)
    return path


def persist_tool_artifact(
    root: str | Path,
    *,
    item_id: str,
    revision: int,
    tool_request: dict[str, Any] | None,
    tool_result_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist reviewable tool source outside ledger metadata and return provenance."""

    root_path = Path(root)
    request = dict(tool_request or {})
    metadata = dict(tool_result_metadata or {})
    tool = str(request.get("tool") or "none").strip().lower()
    summary: dict[str, Any] = {
        "tool": tool,
        "theorem_name": str(request.get("theorem_name") or ""),
        "artifact_path": "",
        "sha256": "",
    }
    if tool in {"lean", "lean_draft"} and str(request.get("source") or ""):
        target = _immutable_write(
            root_path / "formal" / item_id / f"r{int(revision)}.lean",
            str(request["source"]),
        )
        summary["artifact_path"] = str(target.relative_to(root_path))
        summary["sha256"] = sha256_file(target)
    elif tool == "z3" and str(request.get("smt2") or ""):
        target = _immutable_write(
            root_path / "formal" / item_id / f"r{int(revision)}.smt2",
            str(request["smt2"]),
        )
        summary["artifact_path"] = str(target.relative_to(root_path))
        summary["sha256"] = sha256_file(target)
    elif tool == "script":
        summary["artifact_path"] = str(request.get("name") or "")
        summary["sha256"] = str(metadata.get("script_sha256") or "")
    elif tool == "code_experiment":
        summary["artifact_path"] = str(metadata.get("artifact_path") or metadata.get("script_path") or "")
        summary["sha256"] = str(metadata.get("script_sha256") or "")
    return summary
