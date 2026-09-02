from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lab.integrity import sha256_file

if TYPE_CHECKING:
    from lab.research_contract import ResearchContract
    from lab.tools import ToolResult


EVIDENCE_KINDS = {
    "DETERMINISTIC_COUNTEREXAMPLE",
    "EXACT_PASS",
    "EXHAUSTIVE_NO_SOLUTION",
    "EXHAUSTIVE_OPTIMUM",
    "NUMERICAL_PASS",
    "SOLVER_RESULT",
    "FORMAL_PROOF",
    "INCONCLUSIVE",
}
EVIDENCE_ROLES = {"SEARCH_CERTIFICATE", "INDEPENDENT_CHECKER", "GENERAL"}
SOURCE_ORIGINS = {"BUILTIN", "CHECKED_IN", "GENERATED"}
RESOLUTION_SCOPES = {"PARTIAL", "TARGET_RESOLUTION"}
STRONG_PILOT_KINDS = {
    "EXACT_PASS",
    "EXHAUSTIVE_NO_SOLUTION",
    "EXHAUSTIVE_OPTIMUM",
    "DETERMINISTIC_COUNTEREXAMPLE",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def candidate_sha256(candidate: Any) -> str:
    """Hash one candidate artifact using canonical JSON, not presentation bytes."""

    return sha256_json(candidate)


def _module_sha(name: str) -> str:
    path = Path(__file__).with_name(name)
    return sha256_file(path) if path.is_file() else hashlib.sha256(name.encode()).hexdigest()


@dataclass(frozen=True)
class Evidence:
    source: str
    source_origin: str
    evidence_role: str
    resolution_scope: str
    kind: str
    ok: bool
    exhaustive: bool
    termination_reason: str
    witness: dict[str, Any] | None
    contract_hash: str
    target_id: str | None
    target_hash: str | None
    runtime_s: float
    input_sha256: str
    output_sha256: str
    tool_sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_version: int = 1

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind: {self.kind}")
        if self.source_origin not in SOURCE_ORIGINS:
            raise ValueError(f"unknown source_origin: {self.source_origin}")
        if self.evidence_role not in EVIDENCE_ROLES:
            raise ValueError(f"unknown evidence_role: {self.evidence_role}")
        if self.resolution_scope not in RESOLUTION_SCOPES:
            raise ValueError(f"unknown resolution_scope: {self.resolution_scope}")
        if self.target_id and not self.target_hash:
            raise ValueError("target_hash is required when target_id is present")
        if self.kind.startswith("EXHAUSTIVE_") and not self.exhaustive:
            raise ValueError("EXHAUSTIVE evidence requires exhaustive=True")
        if self.kind == "DETERMINISTIC_COUNTEREXAMPLE" and self.witness is None:
            raise ValueError("deterministic counterexample requires a witness")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def evidence_hash(self) -> str:
        return sha256_json(self.as_dict())

    def downgrade(self, reason: str, *, downgraded_from: str | None = None) -> "Evidence":
        metadata = dict(self.metadata)
        metadata["evidence_downgrade_reason"] = reason
        if downgraded_from:
            metadata["downgraded_from"] = downgraded_from
        return replace(
            self,
            kind="INCONCLUSIVE",
            ok=False,
            exhaustive=False,
            witness=None,
            metadata=metadata,
        )


def _script_payload(result: "ToolResult") -> dict[str, Any]:
    payload = result.metadata.get("evidence_payload")
    if isinstance(payload, dict):
        return dict(payload)
    lines = [line.strip() for line in str(result.output or "").splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _source_origin(tool: str) -> str:
    if tool == "script":
        return "CHECKED_IN"
    if tool == "code_experiment":
        return "GENERATED"
    return "BUILTIN"


def _tool_sha(result: "ToolResult") -> str:
    if result.tool == "script":
        value = str(result.metadata.get("script_sha256") or "")
        return value or _module_sha("tools.py")
    if result.tool == "code_experiment":
        return _module_sha("code_experiment.py")
    return _module_sha("tools.py")


def _classify(result: "ToolResult") -> tuple[str, bool, dict[str, Any] | None, dict[str, Any]]:
    tool = result.tool
    metadata = dict(result.metadata or {})
    if tool == "lean":
        if result.ok and metadata.get("formal_verified") is True:
            return "FORMAL_PROOF", False, None, metadata
        return "INCONCLUSIVE", False, None, metadata
    if tool == "z3":
        value = str(metadata.get("result") or "")
        if result.ok and value in {"sat", "unsat"}:
            return "SOLVER_RESULT", False, None, metadata
        return "INCONCLUSIVE", False, None, metadata
    if tool == "tropical_grid":
        status = str(metadata.get("status") or "")
        if status == "COUNTEREXAMPLE":
            return "DETERMINISTIC_COUNTEREXAMPLE", False, metadata, metadata
        if result.ok and status == "GRID_PASS":
            return "NUMERICAL_PASS", False, None, metadata
        return "INCONCLUSIVE", False, None, metadata
    if tool == "script":
        payload = _script_payload(result)
        metadata["evidence_payload"] = payload
        declared = str(payload.get("kind") or "INCONCLUSIVE").upper()
        allowed = {
            str(value).upper()
            for value in metadata.get("allowed_evidence_kinds", ["NUMERICAL_PASS", "INCONCLUSIVE"])
        }
        allowed.add("INCONCLUSIVE")
        if metadata.get("accepts_specification") is True:
            allowed &= {"SOLVER_RESULT", "NUMERICAL_PASS", "INCONCLUSIVE"}
        if "FORMAL_PROOF" in allowed:
            allowed = {"INCONCLUSIVE"}
        if declared not in EVIDENCE_KINDS or declared not in allowed:
            metadata["downgraded_from"] = declared
            return "INCONCLUSIVE", False, None, metadata
        exhaustive = bool(payload.get("exhaustive", False))
        witness = payload.get("witness") if isinstance(payload.get("witness"), dict) else None
        if declared.startswith("EXHAUSTIVE_") and not exhaustive:
            metadata["downgraded_from"] = declared
            return "INCONCLUSIVE", False, None, metadata
        if declared == "DETERMINISTIC_COUNTEREXAMPLE" and witness is None:
            metadata["downgraded_from"] = declared
            return "INCONCLUSIVE", False, None, metadata
        return declared, exhaustive, witness, metadata
    if tool == "code_experiment":
        nested_value = metadata.get("evidence")
        nested: dict[str, Any] = dict(nested_value) if isinstance(nested_value, dict) else {}
        successful_runs = metadata.get("successful_run_count", nested.get("successful_run_count", 0))
        if result.ok and int(successful_runs or 0) > 0:
            return "NUMERICAL_PASS", False, None, metadata
        return "INCONCLUSIVE", False, None, metadata
    return "INCONCLUSIVE", False, None, metadata


def evidence_from_tool_result(
    result: "ToolResult",
    *,
    request: dict[str, Any] | None = None,
    contract: "ResearchContract | None" = None,
    target_id: str | None = None,
) -> Evidence:
    kind, exhaustive, witness, metadata = _classify(result)

    # Preserve legacy behavior for successful checked-in scripts that predate
    # the structured evidence trailer. Contract-bound projects stay fail-closed.
    if contract is None and result.tool == "script" and result.ok and not _script_payload(result):
        kind = "NUMERICAL_PASS"
        exhaustive = False
        witness = None
        metadata.pop("downgraded_from", None)

    target_hash: str | None = None
    resolution_scope = "PARTIAL"
    contract_hash = ""
    if contract is not None:
        contract_hash = contract.contract_hash if contract.frozen else ""
        if target_id:
            target = contract.target(target_id)
            target_hash = target.target_hash
            if target.target_type in {"COMPUTE", "OPTIMIZE"}:
                payload = metadata.get("evidence_payload")
                covered = payload.get("covered") if isinstance(payload, dict) else None
                if not isinstance(covered, dict):
                    covered = metadata.get("covered") if isinstance(metadata.get("covered"), dict) else None
                resolution_scope = contract.resolution_scope(target_id, covered)

    payload = metadata.get("evidence_payload")
    termination_reason = str(payload.get("termination_reason") or "") if isinstance(payload, dict) else ""
    if not termination_reason:
        if kind == "DETERMINISTIC_COUNTEREXAMPLE" and not str(result.error or "").strip():
            termination_reason = "completed"
        elif result.ok:
            termination_reason = "completed"
        elif "timeout" in str(result.error or "").lower():
            termination_reason = "timeout"
        else:
            termination_reason = "error"
    if termination_reason != "completed":
        metadata.setdefault("downgraded_from", kind)
        kind = "INCONCLUSIVE"
        exhaustive = False
        witness = None

    role = str(metadata.get("evidence_role") or "GENERAL").upper()
    if role not in EVIDENCE_ROLES:
        role = "GENERAL"
    input_sha = sha256_json(request or {"tool": result.tool})
    output_sha = sha256_json(result.as_dict())
    runtime_s = float(metadata.get("runtime_s", 0.0) or 0.0)
    semantic_ok = kind != "INCONCLUSIVE" and (
        result.ok or kind == "DETERMINISTIC_COUNTEREXAMPLE"
    )
    return Evidence(
        source=result.tool,
        source_origin=_source_origin(result.tool),
        evidence_role=role,
        resolution_scope=resolution_scope,
        kind=kind,
        ok=semantic_ok,
        exhaustive=exhaustive,
        termination_reason=termination_reason,
        witness=witness,
        contract_hash=contract_hash,
        target_id=target_id,
        target_hash=target_hash,
        runtime_s=runtime_s,
        input_sha256=input_sha,
        output_sha256=output_sha,
        tool_sha256=_tool_sha(result),
        metadata=metadata,
    )


def validate_evidence_binding(
    evidence: Evidence,
    *,
    contract: "ResearchContract | None" = None,
) -> Evidence:
    if evidence.termination_reason != "completed":
        return evidence.downgrade("termination was not completed", downgraded_from=evidence.kind)
    if evidence.source_origin == "GENERATED" and evidence.kind not in {
        "NUMERICAL_PASS",
        "SOLVER_RESULT",
        "INCONCLUSIVE",
    }:
        return evidence.downgrade(
            "generated evidence cannot claim a strong evidence kind",
            downgraded_from=evidence.kind,
        )
    if contract is None:
        return evidence
    if not contract.frozen or evidence.contract_hash != contract.contract_hash:
        return evidence.downgrade("contract hash mismatch", downgraded_from=evidence.kind)
    if evidence.target_id:
        try:
            target = contract.target(evidence.target_id, require_open=True)
        except (KeyError, ValueError):
            return evidence.downgrade("target is missing or not OPEN", downgraded_from=evidence.kind)
        if evidence.target_hash != target.target_hash:
            return evidence.downgrade("target hash mismatch", downgraded_from=evidence.kind)
    numerical_policy = str(contract.evidence_policy.get("numerical") or "OPEN").upper()
    if evidence.kind == "NUMERICAL_PASS" and numerical_policy == "OPEN":
        return evidence.downgrade("contract keeps numerical evidence OPEN", downgraded_from="NUMERICAL_PASS")
    return evidence
