"""Read-only, exact-match research receipts; never a scientific truth database."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 8_000_000
MAX_CLAIMS = 10_000


def _normalized(value: Any) -> Any:
    # Preserve mathematical case, punctuation, ordering and internal whitespace.
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in sorted(value.items())}
    return value


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _identity(statement: str, domain: Any, assumptions: Any) -> tuple[str, dict]:
    binding = _normalized({"statement": statement, "domain": domain, "assumptions": assumptions})
    return hashlib.sha256(_encoded(binding)).hexdigest(), binding


def _safe_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Evidence path must stay within its explicit run folder")
    if any(part.lower().startswith(".env") or any(word in part.lower() for word in ("secret", "credential", "private")) for part in candidate.parts):
        raise ValueError("Private and environment files are forbidden")
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents):
        raise ValueError("Symlink sources are forbidden")
    candidate.resolve().relative_to(root.resolve())
    return candidate


def _receipt(root: Path, relative: str) -> tuple[dict, bytes | None]:
    path = _safe_path(root, relative)
    receipt: dict[str, Any] = {"run_folder": str(root), "path": relative}
    if not path.is_file():
        return {**receipt, "state": "MISSING"}, None
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Research memory source exceeds byte limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Research memory source exceeds byte limit")
    return {**receipt, "state": "READ", "sha256": hashlib.sha256(data).hexdigest()}, data


def build_memory(run_folders: list[Path]) -> dict:
    """Read package/CLAIM_LEDGER.json only from explicitly selected run folders.

    Supporting lane classifications are retained, never counted as independent
    proofs. Missing bindings remain unknown and cannot establish novelty.
    """
    records: dict[str, dict] = {}
    sources: dict[bytes, dict] = {}
    warnings: list[str] = []
    claim_count = 0
    for supplied in sorted(set(map(str, run_folders))):
        root = Path(supplied).absolute()
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            raise ValueError("Symlink run folders are forbidden")
        relative = "package/CLAIM_LEDGER.json"
        if not _safe_path(root, relative).exists():
            relative = "CLAIM_LEDGER.json"
        receipt, data = _receipt(root, relative)
        sources[_encoded(receipt)] = receipt
        if data is None:
            warnings.append(f"No claim ledger: {root}")
            continue
        ledger = json.loads(data)
        if not isinstance(ledger, dict):
            raise ValueError("Claim ledger must be an object")
        claims = ledger.get("claims", [])
        if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
            raise ValueError("Invalid or oversized claim ledger")
        claim_count += len(claims)
        if claim_count > MAX_CLAIMS:
            raise ValueError("Research memory exceeds total claim limit")
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("statement"), str):
                raise ValueError("Claim must contain a statement")
            if not isinstance(claim.get("status", "UNKNOWN"), str):
                raise ValueError("Claim status must be a string")
            fingerprint, binding = _identity(claim["statement"], claim.get("domain"), claim.get("assumptions"))
            row = records.setdefault(fingerprint, {"fingerprint": fingerprint, **binding, "observations": []})
            references = []
            evidence_names = claim.get("evidence_files", [])
            supporting_items = claim.get("supporting_evidence", [])
            if not isinstance(evidence_names, list) or not isinstance(supporting_items, list):
                raise ValueError("Evidence references must be lists")
            evidence_names = list(evidence_names)
            for supporting in supporting_items:
                if isinstance(supporting, dict):
                    names = supporting.get("evidence_files", [])
                    if not isinstance(names, list):
                        raise ValueError("Supporting evidence references must be a list")
                    evidence_names.extend(names)
                else:
                    raise ValueError("Supporting evidence must be an object")
            if len(evidence_names) > 1000:
                raise ValueError("Too many evidence references")
            for name in evidence_names:
                if not isinstance(name, str):
                    raise ValueError("Evidence reference must be a path string")
                prefix = "package/" if relative.startswith("package/") else ""
                ref, _ = _receipt(root, prefix + name)
                sources[_encoded(ref)] = ref
                if ref not in references:
                    references.append(ref)
            row["observations"].append({
                "claim_id": claim.get("claim_id"), "reported_status": claim.get("status", "UNKNOWN"),
                "first_missing_step": claim.get("first_missing_step"), "source": receipt,
                "evidence": references, "supporting_evidence": claim.get("supporting_evidence", []),
                "source_conflict": bool(claim.get("classification_conflict") or claim.get("semantic_binding_conflict")),
            })
    for row in records.values():
        row["observations"] = sorted({_encoded(item): item for item in row["observations"]}.values(), key=_encoded)
        statuses = sorted({item["reported_status"] for item in row["observations"]})
        row["reported_statuses"] = statuses
        row["disagreement"] = len(statuses) > 1 or any(item["source_conflict"] for item in row["observations"])
        row["binding_complete"] = row["domain"] is not None and row["assumptions"] is not None
        row["missing_steps"] = sorted({item["first_missing_step"] for item in row["observations"] if isinstance(item["first_missing_step"], str)})
        row["scientific_verdict"] = "NOT_ASSESSED"
    return {
        "schema_version": "research-memory-1", "matching": "EXACT_NORMALIZED_BINDING_ONLY",
        "truth_verified": False, "novelty_assessed": False,
        "limitations": "Reported classifications are receipts, not votes or proofs. Lane failure/dropping is not scientific refutation. Shared code and definitions do not constitute independent proof.",
        "records": [records[key] for key in sorted(records)],
        "sources": [sources[key] for key in sorted(sources)], "warnings": sorted(set(warnings)),
    }


def check_proposal(memory: dict, statement: str, domain: Any, assumptions: Any) -> dict:
    fingerprint, _ = _identity(statement, domain, assumptions)
    matches = [row for row in memory.get("records", []) if row.get("fingerprint") == fingerprint]
    return {"fingerprint": fingerprint, "match": "EXACT_BINDING_MATCH" if matches else "NO_EXACT_MATCH",
            "novelty_assessed": False, "binding_complete": domain is not None and assumptions is not None,
            "records": matches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--run", action="append", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (any(part.lower().startswith(".env") for part in args.output.parts)
            or args.output.is_symlink() or any(parent.is_symlink() for parent in args.output.parents)):
        parser.error("Unsafe output path")
    result = build_memory(args.run)
    # Exclusive creation prevents overwriting any frozen experiment artifact.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
