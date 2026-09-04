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
from lab.ledger_semantics import (
    claim_hash,
    clear_revision_bound_metadata,
    persist_tool_artifact,
    revision_record,
    set_active_revision_binding,
    sync_current_revision,
    verification_claim_annotation,
)
from lab.research_contract import ResearchContract
from lab.trace import get_active_trace


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
    current_revision: int = 1
    revisions: list[dict[str, Any]] = field(default_factory=list)


class ResearchState:
    """Inspectable append-only research ledger with revision-bound evidence.

    ``state.json`` remains human-readable. A current PROVEN record additionally
    carries an HMAC proof seal over the bound Lean evidence. Historical revisions
    retain their own status/evidence/proof metadata so revising a claim never
    silently transfers a previous proof to the new text.
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

    @staticmethod
    def _trace(event_type: str, **payload: Any) -> None:
        trace = get_active_trace()
        if trace is not None:
            trace.log(event_type, **payload)

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
        claim_sha = hashlib.sha256(claim.encode("utf-8")).hexdigest()
        if str(metadata.get("claim_sha256") or "") != claim_sha:
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

    def _revision_proof_seal_valid(self, item_id: str, revision: dict[str, Any]) -> tuple[bool, str]:
        claim = str(revision.get("claim") or "")
        metadata = dict(revision.get("formal_metadata") or {})
        valid, reason = self._validate_live_formal_binding(item_id, claim, metadata)
        if not valid:
            return False, reason
        payload = self._proof_payload(item_id, claim, metadata)
        if not self.signer.verify("proven_evidence:v1", payload, str(metadata.get("proof_seal") or "")):
            return False, "proof HMAC seal invalid or missing"
        return True, ""

    @staticmethod
    def _revision_iteration(raw: dict[str, Any]) -> int | None:
        metadata = dict(raw.get("metadata") or {})
        value = metadata.get("iteration")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _ensure_revision_fields(self, raw: dict[str, Any]) -> None:
        current = int(raw.get("current_revision", 1) or 1)
        revisions = raw.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            revisions = [
                revision_record(
                    revision=current,
                    title=str(raw.get("title") or ""),
                    claim=str(raw.get("claim") or ""),
                    status=str(raw.get("status") or "OPEN"),
                    created_at=str(raw.get("created_at") or _now()),
                    iteration=self._revision_iteration(raw),
                    metadata=dict(raw.get("metadata") or {}),
                )
            ]
        raw["current_revision"] = current
        raw["revisions"] = revisions

    def _prepare_proposal_metadata(
        self,
        item_id: str,
        revision: int,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata)
        raw_proposal = merged.get("proposal")
        if not isinstance(raw_proposal, dict):
            return merged
        proposal = dict(raw_proposal)
        raw_request = proposal.get("tool_request")
        tool_request = dict(raw_request) if isinstance(raw_request, dict) else None
        summary = persist_tool_artifact(
            self.root,
            item_id=item_id,
            revision=revision,
            tool_request=tool_request,
        )
        if tool_request is not None:
            sanitized_request = {
                key: value
                for key, value in tool_request.items()
                if key not in {"source", "smt2"}
            }
            proposal["tool_request"] = sanitized_request
        merged["proposal"] = proposal
        merged["strategy"] = proposal.get("strategy")
        merged["evidence_needed"] = list(proposal.get("evidence_needed") or [])
        merged["tool_request_summary"] = summary
        return merged

    def _bind_current_evidence(
        self,
        item_id: str,
        claim: str,
        revision: int,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata)
        raw_evidence = merged.get("evidence")
        if not isinstance(raw_evidence, dict):
            return merged
        evidence = dict(raw_evidence)
        expected_hash = claim_hash(claim)
        evidence_hash = str(evidence.get("claim_hash") or "")
        evidence_revision = evidence.get("revision")
        unbound = not evidence_hash and evidence_revision in {None, ""}
        revision_matches = False
        if evidence_revision not in {None, ""}:
            try:
                revision_matches = int(evidence_revision) == int(revision)
            except (TypeError, ValueError):
                revision_matches = False
        if unbound:
            evidence["claim_hash"] = expected_hash
            evidence["revision"] = int(revision)
        elif evidence_hash != expected_hash or not revision_matches:
            merged.pop("evidence", None)
            rejected = list(merged.get("rejected_revision_evidence") or [])
            rejected.append(evidence)
            merged["rejected_revision_evidence"] = rejected[-8:]
            self._trace(
                "revision_evidence_rejected",
                item_id=item_id,
                revision=int(revision),
                expected_claim_hash=expected_hash,
                evidence_claim_hash=evidence_hash,
                evidence_revision=evidence_revision,
            )
            return merged
        merged["evidence"] = evidence

        summary = dict(merged.get("tool_request_summary") or {})
        if summary and not str(summary.get("sha256") or ""):
            evidence_meta = evidence.get("metadata")
            evidence_metadata = dict(evidence_meta) if isinstance(evidence_meta, dict) else {}
            summary["sha256"] = str(
                evidence_metadata.get("script_sha256")
                or evidence_metadata.get("lean_sha256")
                or evidence.get("tool_sha256")
                or ""
            )
            merged["tool_request_summary"] = summary
        return merged

    def _annotate_claim(
        self,
        item_id: str,
        title: str,
        claim: str,
        status: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata)
        annotation = verification_claim_annotation(
            title,
            claim,
            status=status,
            metadata=merged,
        )
        prior = {
            key: merged.get(key)
            for key in (
                "self_verification_claim",
                "verification_claim_supported",
                "verification_claim_terms",
            )
        }
        merged.update(annotation)
        if annotation["self_verification_claim"] and any(
            prior.get(key) != merged.get(key)
            for key in prior
        ):
            self._trace(
                "self_verification_claim_flagged",
                item_id=item_id,
                status=status,
                supported=annotation["verification_claim_supported"],
                terms=annotation["verification_claim_terms"],
            )
        return merged

    def _read_state(self) -> dict[str, Any]:
        raw = read_json_tolerant(self.state_path, {"items": [], "events": []})
        if not isinstance(raw, dict):
            return {"items": [], "events": []}
        raw.setdefault("items", [])
        raw.setdefault("events", [])
        for item in raw.get("items", []):
            if not isinstance(item, dict):
                continue
            self._ensure_revision_fields(item)
            if str(item.get("status") or "") == "PROVEN":
                valid, reason = self._proof_seal_valid(item)
                if not valid:
                    item["status"] = "PROOF_CANDIDATE"
                    metadata = dict(item.get("metadata") or {})
                    metadata["integrity_warning"] = f"Stored PROVEN downgraded: {reason}"
                    metadata["formal_verified"] = False
                    item["metadata"] = metadata
            current = int(item.get("current_revision", 1) or 1)
            revisions = []
            for entry in item.get("revisions", []):
                revision = dict(entry)
                if str(revision.get("status") or "") == "PROVEN":
                    valid, reason = self._revision_proof_seal_valid(str(item.get("id") or ""), revision)
                    if not valid:
                        revision["status"] = "PROOF_CANDIDATE"
                        revision["integrity_warning"] = f"Stored PROVEN revision downgraded: {reason}"
                        if int(revision.get("revision", 0) or 0) == current:
                            item["status"] = "PROOF_CANDIDATE"
                revisions.append(revision)
            item["revisions"] = revisions
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
                "current_revision": int(item.get("current_revision", 1) or 1),
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
        base_metadata = dict(metadata or {})
        raw_proposal = base_metadata.get("proposal")
        proposal = dict(raw_proposal) if isinstance(raw_proposal, dict) else {}
        revises = str(proposal.get("revises") or "").strip()
        if kind == "conjecture" and revises:
            try:
                existing = self.get(revises)
            except KeyError:
                existing = None
            if existing is not None and existing.kind == "conjecture":
                iteration = self._revision_iteration({"metadata": base_metadata})
                if iteration is None:
                    iteration = existing.current_revision + 1
                return self.revise_item(
                    revises,
                    title=title,
                    claim=claim,
                    iteration=iteration,
                    metadata=base_metadata,
                )
            self._trace(
                "revision_target_invalid",
                item_id=revises,
                iteration=self._revision_iteration({"metadata": base_metadata}),
            )

        item_id = self._new_id(kind)
        clean_title = title.strip()
        clean_claim = claim.strip()
        merged_metadata = self._prepare_proposal_metadata(item_id, 1, base_metadata)
        merged_metadata = self._bind_current_evidence(
            item_id,
            clean_claim,
            1,
            merged_metadata,
        )
        merged_metadata = self._annotate_claim(
            item_id,
            clean_title,
            clean_claim,
            status,
            merged_metadata,
        )
        if status == "PROVEN":
            merged_metadata = self._seal_proven(item_id, clean_claim, merged_metadata)
            merged_metadata = self._annotate_claim(
                item_id,
                clean_title,
                clean_claim,
                status,
                merged_metadata,
            )
        created_at = _now()
        revision = revision_record(
            revision=1,
            title=clean_title,
            claim=clean_claim,
            status=status,
            created_at=created_at,
            iteration=self._revision_iteration({"metadata": merged_metadata}),
            metadata=merged_metadata,
        )
        item = ResearchItem(
            id=item_id,
            kind=kind,
            title=clean_title,
            claim=clean_claim,
            status=status,
            evidence=list(evidence or []),
            dependencies=list(dependencies or []),
            metadata=merged_metadata,
            created_at=created_at,
            updated_at=created_at,
            current_revision=1,
            revisions=[revision],
        )
        data = self._read_state()
        data["items"].append(asdict(item))
        data["events"].append({"ts": _now(), "type": "item_added", "item_id": item.id})
        self._write_state(data)
        if kind == "conjecture":
            set_active_revision_binding(item.id, item.claim, item.current_revision)
        return item

    def get(self, item_id: str) -> ResearchItem:
        for raw in self._read_state()["items"]:
            if raw["id"] == item_id:
                self._ensure_revision_fields(raw)
                item = ResearchItem(**raw)
                if item.kind == "conjecture":
                    set_active_revision_binding(item.id, item.claim, item.current_revision)
                return item
        raise KeyError(item_id)

    def revise_item(
        self,
        item_id: str,
        *,
        title: str,
        claim: str,
        iteration: int,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchItem:
        clean_title = str(title or "").strip()
        clean_claim = str(claim or "").strip()
        if not clean_title or not clean_claim:
            raise ValueError("revision requires non-empty title and claim")
        data = self._read_state()
        for raw in data["items"]:
            if raw.get("id") != item_id:
                continue
            if str(raw.get("kind") or "") != "conjecture":
                break
            self._ensure_revision_fields(raw)
            current = int(raw.get("current_revision", 1) or 1)
            existing_metadata = dict(raw.get("metadata") or {})
            raw["revisions"] = sync_current_revision(
                list(raw.get("revisions") or []),
                current_revision=current,
                title=str(raw.get("title") or ""),
                claim=str(raw.get("claim") or ""),
                status=str(raw.get("status") or "OPEN"),
                metadata=existing_metadata,
            )
            next_revision = current + 1
            next_metadata = clear_revision_bound_metadata(existing_metadata)
            next_metadata.update(dict(metadata or {}))
            next_metadata["iteration"] = int(iteration)
            next_metadata = self._prepare_proposal_metadata(
                item_id,
                next_revision,
                next_metadata,
            )
            next_metadata = self._bind_current_evidence(
                item_id,
                clean_claim,
                next_revision,
                next_metadata,
            )
            next_metadata = self._annotate_claim(
                item_id,
                clean_title,
                clean_claim,
                "OPEN",
                next_metadata,
            )
            raw["title"] = clean_title
            raw["claim"] = clean_claim
            raw["status"] = "OPEN"
            raw["metadata"] = next_metadata
            raw["current_revision"] = next_revision
            raw.setdefault("revisions", []).append(
                revision_record(
                    revision=next_revision,
                    title=clean_title,
                    claim=clean_claim,
                    status="OPEN",
                    created_at=_now(),
                    iteration=int(iteration),
                    metadata=next_metadata,
                )
            )
            raw["updated_at"] = _now()
            data["events"].append(
                {
                    "ts": _now(),
                    "type": "item_revised",
                    "item_id": item_id,
                    "revision": next_revision,
                    "iteration": int(iteration),
                }
            )
            self._write_state(data)
            self._trace(
                "item_revision_added",
                item_id=item_id,
                revision=next_revision,
                iteration=int(iteration),
            )
            item = ResearchItem(**raw)
            set_active_revision_binding(item.id, item.claim, item.current_revision)
            return item
        self._trace("revision_target_invalid", item_id=item_id, iteration=int(iteration))
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
            self._ensure_revision_fields(raw)
            effective_status = status or str(raw.get("status") or "OPEN")
            merged_metadata = {**raw.get("metadata", {}), **(metadata or {})}
            merged_metadata = self._bind_current_evidence(
                item_id,
                str(raw.get("claim") or ""),
                int(raw.get("current_revision", 1) or 1),
                merged_metadata,
            )
            merged_metadata = self._annotate_claim(
                item_id,
                str(raw.get("title") or ""),
                str(raw.get("claim") or ""),
                effective_status,
                merged_metadata,
            )
            if status == "PROVEN":
                merged_metadata = self._seal_proven(item_id, str(raw.get("claim") or ""), merged_metadata)
                merged_metadata = self._annotate_claim(
                    item_id,
                    str(raw.get("title") or ""),
                    str(raw.get("claim") or ""),
                    effective_status,
                    merged_metadata,
                )
            if status:
                raw["status"] = status
            if evidence:
                additions = [evidence] if isinstance(evidence, str) else evidence
                raw.setdefault("evidence", []).extend(str(x) for x in additions)
            raw["metadata"] = merged_metadata
            raw["revisions"] = sync_current_revision(
                list(raw.get("revisions") or []),
                current_revision=int(raw.get("current_revision", 1) or 1),
                title=str(raw.get("title") or ""),
                claim=str(raw.get("claim") or ""),
                status=str(raw.get("status") or "OPEN"),
                metadata=merged_metadata,
            )
            raw["updated_at"] = _now()
            data["events"].append(
                {"ts": _now(), "type": "item_updated", "item_id": item_id, "status": status}
            )
            self._write_state(data)
            item = ResearchItem(**raw)
            if item.kind == "conjecture":
                set_active_revision_binding(item.id, item.claim, item.current_revision)
            return item
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
        items = []
        for raw in self._read_state()["items"]:
            self._ensure_revision_fields(raw)
            items.append(ResearchItem(**raw))
        if kind:
            items = [x for x in items if x.kind == kind]
        if status:
            items = [x for x in items if x.status == status]
        conjectures = [item for item in items if item.kind == "conjecture"]
        if conjectures:
            def iteration_key(item: ResearchItem) -> tuple[int, str]:
                try:
                    iteration = int(item.metadata.get("iteration", -1) or -1)
                except (TypeError, ValueError):
                    iteration = -1
                return iteration, item.updated_at

            active = max(conjectures, key=iteration_key)
            set_active_revision_binding(active.id, active.claim, active.current_revision)
        return items

    @staticmethod
    def _line(item: ResearchItem, claim_limit: int = 260) -> str:
        claim = item.claim.replace("\n", " ")
        if len(claim) > claim_limit:
            claim = claim[: claim_limit - 3] + "..."
        claim_tag = " [İDDİA]" if item.metadata.get("self_verification_claim") and not item.metadata.get("verification_claim_supported") else ""
        revision_tag = f" r{item.current_revision}" if item.current_revision > 1 else ""
        return f"[{item.id}] [{item.status}]{revision_tag}{claim_tag} {item.title}: {claim}"

    def summary_for_prompt(self, limit: int = 20) -> str:
        items = self.list_items()[-limit:]
        if not items:
            return "(henüz kayıtlı araştırma iddiası yok)"
        return "\n".join(self._line(item) for item in items)

    def research_context(self, *, recent_limit: int = 16, fail_claim_chars: int = 120) -> str:
        """Selective context that never forgets rejected ideas or historical revisions."""

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
            historical = [
                (item, revision)
                for item in live
                for revision in item.revisions
                if int(revision.get("revision", 0) or 0) != item.current_revision
            ]
            if historical:
                lines.append("HISTORICAL REVISIONS - evidence remains bound to its own claim_hash:")
                for item, revision in historical[-max(0, int(recent_limit)) :]:
                    lines.append(
                        f"[{item.id}] r{revision.get('revision')} [{revision.get('status', 'OPEN')}] "
                        f"claim_hash={revision.get('claim_hash', '')}: {revision.get('claim', '')}"
                    )
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
