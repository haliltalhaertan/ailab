from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import EvidenceSigner, atomic_write_json, read_json_tolerant, sha256_file
from lab.research_contract import ResearchContract


VALID_STATUSES = {
    "OPEN",
    "REFUTATION_CANDIDATE",
    "COMPUTATION_PASS",
    "PROOF_CANDIDATE",
    "PROVEN",
    "FAIL",
    "KNOWN",
    "BARRIER",
    "DROPPED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_label(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return value[:80] or "checkpoint"


@dataclass
class ResearchItem:
    id: str
    kind: str
    title: str
    claim: str
    status: str = "OPEN"
    evidence: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class ResearchState:
    """Inspectable research ledger with explicit evidence gates for PROVEN.

    ``state.json`` remains human-readable. A PROVEN record additionally carries
    an HMAC proof seal over the bound Lean evidence and is revalidated against the
    project-local Lean file whenever the ledger is read.
    """

    def __init__(self, root: str | Path = "research_state/default"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir = self.root / "checkpoints"
        self.checkpoint_dir.mkdir(exist_ok=True)
        self.state_path = self.root / "state.json"
        self.problem_path = self.root / "problem_frozen.json"
        self.graph_path = self.root / "theorem_graph.json"
        self.signer = EvidenceSigner(self.root)
        if not self.state_path.exists():
            self._write_state({"items": [], "events": []})

    def _proof_payload(self, item_id: str, claim: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": item_id,
            "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            "lean_file": str(metadata.get("lean_file") or metadata.get("file") or ""),
            "lean_sha256": str(metadata.get("lean_sha256") or ""),
            "theorem_name": str(metadata.get("theorem_name") or ""),
            "theorem_type": str(metadata.get("theorem_type") or ""),
            "iteration": metadata.get("iteration"),
            "axioms": list(metadata.get("axioms") or []),
            "formal_verified": bool(metadata.get("formal_verified")),
            "formal_binding_verified": bool(metadata.get("formal_binding_verified")),
            "axioms_verified": bool(metadata.get("axioms_verified")),
            "source_clean": bool(metadata.get("source_clean")),
        }

    def _validate_live_formal_binding(
        self,
        item_id: str,
        claim: str,
        metadata: dict[str, Any],
    ) -> tuple[bool, str]:
        if not (
            metadata.get("formal_verified") is True
            and metadata.get("formal_binding_verified") is True
            and metadata.get("axioms_verified") is True
            and metadata.get("source_clean") is True
        ):
            return False, "formal evidence flags incomplete"
        if str(metadata.get("item_id") or "") != item_id:
            return False, "formal evidence item_id mismatch"
        claim_hash = hashlib.sha256(claim.encode("utf-8")).hexdigest()
        if str(metadata.get("claim_sha256") or "") != claim_hash:
            return False, "formal evidence claim hash mismatch"
        filename = Path(str(metadata.get("lean_file") or metadata.get("file") or "")).name
        if not filename:
            return False, "formal evidence file missing"
        candidate = (self.root / "formal" / "candidates" / filename).resolve()
        candidate_root = (self.root / "formal" / "candidates").resolve()
        try:
            candidate.relative_to(candidate_root)
        except ValueError:
            return False, "formal evidence file escaped project candidate root"
        if not candidate.is_file():
            return False, "bound Lean file missing"
        if sha256_file(candidate) != str(metadata.get("lean_sha256") or ""):
            return False, "bound Lean file SHA-256 changed"
        if not str(metadata.get("theorem_name") or "").strip() or not str(
            metadata.get("theorem_type") or ""
        ).strip():
            return False, "formal statement binding missing"
        return True, ""

    def _seal_proven(self, item_id: str, claim: str, metadata: dict[str, Any]) -> dict[str, Any]:
        merged = dict(metadata)
        valid, reason = self._validate_live_formal_binding(item_id, claim, merged)
        if not valid:
            raise ValueError(f"PROVEN formal evidence geçersiz: {reason}")
        payload = self._proof_payload(item_id, claim, merged)
        merged["proof_seal"] = self.signer.sign("proven_evidence:v1", payload)
        merged["evidence_key_mode"] = self.signer.mode
        merged["sealed_at"] = _now()
        return merged

    def _proof_seal_valid(self, raw: dict[str, Any]) -> tuple[bool, str]:
        metadata = dict(raw.get("metadata") or {})
        item_id = str(raw.get("id") or "")
        claim = str(raw.get("claim") or "")
        valid, reason = self._validate_live_formal_binding(item_id, claim, metadata)
        if not valid:
            return False, reason
        payload = self._proof_payload(item_id, claim, metadata)
        if not self.signer.verify("proven_evidence:v1", payload, str(metadata.get("proof_seal") or "")):
            return False, "proof HMAC seal invalid or missing"
        return True, ""

    def _read_state(self) -> dict[str, Any]:
        raw = read_json_tolerant(self.state_path, {"items": [], "events": []})
        if not isinstance(raw, dict):
            return {"items": [], "events": []}
        raw.setdefault("items", [])
        raw.setdefault("events", [])
        for item in raw.get("items", []):
            if not isinstance(item, dict) or str(item.get("status") or "") != "PROVEN":
                continue
            valid, reason = self._proof_seal_valid(item)
            if not valid:
                item["status"] = "PROOF_CANDIDATE"
                metadata = dict(item.get("metadata") or {})
                metadata["integrity_warning"] = f"Stored PROVEN downgraded: {reason}"
                metadata["formal_verified"] = False
                item["metadata"] = metadata
        return raw

    def _write_state(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.state_path, data)
        self._write_graph(data)

    def _write_graph(self, data: dict[str, Any]) -> None:
        nodes = [
            {
                "id": item["id"],
                "kind": item["kind"],
                "title": item["title"],
                "status": item["status"],
            }
            for item in data.get("items", [])
        ]
        edges = []
        for item in data.get("items", []):
            for dep in item.get("dependencies", []):
                edges.append({"from": dep, "to": item["id"], "type": "depends_on"})
        atomic_write_json(self.graph_path, {"nodes": nodes, "edges": edges})

    def revision(self) -> str:
        data = self._read_state()
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def freeze_problem(
        self,
        problem: str,
        metadata: dict[str, Any] | None = None,
        *,
        overwrite: bool = False,
    ) -> None:
        contract = ResearchContract.load_optional(self.root)
        merged_metadata = dict(metadata or {})
        if contract is not None:
            if not contract.frozen:
                raise ValueError("Research contract must be frozen before a research run.")
            if contract.problem.strip() != problem.strip():
                raise ValueError("Research contract problem does not match problem_frozen.json / run problem.")
            merged_metadata["contract_hash"] = contract.contract_hash

        payload = {"problem": problem.strip(), "metadata": merged_metadata, "frozen_at": _now()}
        if self.problem_path.exists() and not overwrite:
            existing = read_json_tolerant(self.problem_path, {})
            if str(existing.get("problem", "")).strip() != problem.strip():
                raise ValueError(
                    "Bu research_state başka bir problem için dondurulmuş. Yeni bir project_id kullan veya overwrite=True ver."
                )
            if contract is not None:
                existing_metadata = dict(existing.get("metadata") or {})
                bound_hash = str(existing_metadata.get("contract_hash") or "")
                if bound_hash and bound_hash != contract.contract_hash:
                    raise ValueError("Research contract hash mismatch against the frozen project binding.")
                if not bound_hash:
                    existing_metadata["contract_hash"] = contract.contract_hash
                    existing["metadata"] = existing_metadata
                    atomic_write_json(self.problem_path, existing)
            return
        atomic_write_json(self.problem_path, payload)

    def frozen_problem(self) -> dict[str, Any] | None:
        if not self.problem_path.exists():
            return None
        value = read_json_tolerant(self.problem_path, None)
        return value if isinstance(value, dict) else None

    def _new_id(self, kind: str) -> str:
        prefix = {
            "conjecture": "C",
            "lemma": "L",
            "counterexample": "X",
            "known_result": "K",
            "audit": "A",
            "experiment": "E",
        }.get(kind, "R")
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    def add_item(
        self,
        kind: str,
        title: str,
        claim: str,
        *,
        status: str = "OPEN",
        evidence: list[str] | None = None,
        dependencies: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchItem:
        if status not in VALID_STATUSES:
            raise ValueError(f"Geçersiz status: {status}")
        item_id = self._new_id(kind)
        merged_metadata = dict(metadata or {})
        if status == "PROVEN":
            merged_metadata = self._seal_proven(item_id, claim.strip(), merged_metadata)
        item = ResearchItem(
            id=item_id,
            kind=kind,
            title=title.strip(),
            claim=claim.strip(),
            status=status,
            evidence=list(evidence or []),
            dependencies=list(dependencies or []),
            metadata=merged_metadata,
        )
        data = self._read_state()
        data["items"].append(asdict(item))
        data["events"].append({"ts": _now(), "type": "item_added", "item_id": item.id})
        self._write_state(data)
        return item

    def get(self, item_id: str) -> ResearchItem:
        for raw in self._read_state()["items"]:
            if raw["id"] == item_id:
                return ResearchItem(**raw)
        raise KeyError(item_id)

    def update_item(
        self,
        item_id: str,
        *,
        status: str | None = None,
        evidence: str | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchItem:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Geçersiz status: {status}")
        data = self._read_state()
        for raw in data["items"]:
            if raw["id"] != item_id:
                continue
            merged_metadata = {**raw.get("metadata", {}), **(metadata or {})}
            if status == "PROVEN":
                merged_metadata = self._seal_proven(item_id, str(raw.get("claim") or ""), merged_metadata)
            if status:
                raw["status"] = status
            if evidence:
                additions = [evidence] if isinstance(evidence, str) else evidence
                raw.setdefault("evidence", []).extend(str(x) for x in additions)
            raw["metadata"] = merged_metadata
            raw["updated_at"] = _now()
            data["events"].append(
                {"ts": _now(), "type": "item_updated", "item_id": item_id, "status": status}
            )
            self._write_state(data)
            return ResearchItem(**raw)
        raise KeyError(item_id)

    def add_counterexample(
        self,
        target_id: str,
        description: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ResearchItem:
        self.get(target_id)
        item = self.add_item(
            "counterexample",
            title=f"Counterexample for {target_id}",
            claim=description,
            status="KNOWN",
            dependencies=[target_id],
            metadata={"target_id": target_id, "payload": payload or {}},
        )
        self.update_item(
            target_id,
            status="FAIL",
            evidence=f"Counterexample {item.id}: {description}",
        )
        return item

    def list_items(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[ResearchItem]:
        items = [ResearchItem(**x) for x in self._read_state()["items"]]
        if kind:
            items = [x for x in items if x.kind == kind]
        if status:
            items = [x for x in items if x.status == status]
        return items

    @staticmethod
    def _line(item: ResearchItem, claim_limit: int = 260) -> str:
        claim = item.claim.replace("\n", " ")
        if len(claim) > claim_limit:
            claim = claim[: claim_limit - 3] + "..."
        return f"[{item.id}] [{item.status}] {item.title}: {claim}"

    def summary_for_prompt(self, limit: int = 20) -> str:
        items = self.list_items()[-limit:]
        if not items:
            return "(henüz kayıtlı araştırma iddiası yok)"
        return "\n".join(self._line(item) for item in items)

    def research_context(self, *, recent_limit: int = 16, fail_claim_chars: int = 120) -> str:
        """Selective context that never forgets rejected ideas."""

        items = self.list_items()
        if not items:
            return "(henüz kayıtlı araştırma iddiası yok)"
        dead = [
            x
            for x in items
            if x.kind == "conjecture" and x.status in {"FAIL", "DROPPED"}
        ]
        live = [
            x
            for x in items
            if x.kind == "conjecture" and x.status not in {"FAIL", "DROPPED"}
        ]
        recent = [x for x in items if x.kind != "conjecture"][-max(0, int(recent_limit)) :]
        lines: list[str] = []
        if dead:
            lines.append("REJECTED IDEAS - DO NOT REOPEN:")
            for item in dead:
                claim = item.claim.replace("\n", " ")
                if len(claim) > fail_claim_chars:
                    claim = claim[: fail_claim_chars - 3] + "..."
                lines.append(f"[{item.id}] [{item.status}] {item.title}: {claim}")
        if live:
            lines.append("ACTIVE CANDIDATES:")
            lines.extend(self._line(item) for item in live)
        if recent:
            lines.append("RECENT KNOWN/AUDIT/COUNTEREXAMPLE RECORDS:")
            lines.extend(self._line(item, 180) for item in recent)
        return "\n".join(lines)

    def checkpoint(self, label: str, note: str = "") -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        contract = ResearchContract.load_optional(self.root)
        payload = {
            "created_at": _now(),
            "label": label,
            "note": note,
            "problem": self.frozen_problem(),
            "contract_hash": contract.contract_hash if contract is not None and contract.frozen else "",
            "state": self._read_state(),
            "revision": self.revision(),
        }
        path = self.checkpoint_dir / f"{stamp}_{_safe_label(label)}.json"
        atomic_write_json(path, payload)
        return path
