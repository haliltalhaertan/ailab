from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATUSES = {
    "OPEN",
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

    ``state.json`` remains the human-readable ledger. High-frequency cache and
    partial-response data live in SQLite (StepStore), so the ledger is no longer
    rewritten per streamed token. The revision hash allows an iteration to freeze
    exactly which ledger context it saw before making a proposal.
    """

    def __init__(self, root: str | Path = "research_state/default"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir = self.root / "checkpoints"
        self.checkpoint_dir.mkdir(exist_ok=True)
        self.state_path = self.root / "state.json"
        self.problem_path = self.root / "problem_frozen.json"
        self.graph_path = self.root / "theorem_graph.json"
        if not self.state_path.exists():
            self._write_state({"items": [], "events": []})

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"items": [], "events": []}
        if not isinstance(raw, dict):
            return {"items": [], "events": []}
        raw.setdefault("items", [])
        raw.setdefault("events", [])
        return raw

    def _write_state(self, data: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)
        self._write_graph(data)

    def _write_graph(self, data: dict[str, Any]) -> None:
        nodes = [
            {"id": item["id"], "kind": item["kind"], "title": item["title"], "status": item["status"]}
            for item in data.get("items", [])
        ]
        edges = []
        for item in data.get("items", []):
            for dep in item.get("dependencies", []):
                edges.append({"from": dep, "to": item["id"], "type": "depends_on"})
        tmp = self.graph_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.graph_path)

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
        payload = {"problem": problem.strip(), "metadata": metadata or {}, "frozen_at": _now()}
        if self.problem_path.exists() and not overwrite:
            existing = json.loads(self.problem_path.read_text(encoding="utf-8"))
            if existing.get("problem", "").strip() != problem.strip():
                raise ValueError(
                    "Bu research_state başka bir problem için dondurulmuş. Yeni bir project_id kullan veya overwrite=True ver."
                )
            return
        self.problem_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def frozen_problem(self) -> dict[str, Any] | None:
        if not self.problem_path.exists():
            return None
        return json.loads(self.problem_path.read_text(encoding="utf-8"))

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
        if status == "PROVEN" and not (metadata or {}).get("formal_verified"):
            raise ValueError("PROVEN için formal_verified metadata zorunludur.")
        item = ResearchItem(
            id=self._new_id(kind),
            kind=kind,
            title=title.strip(),
            claim=claim.strip(),
            status=status,
            evidence=list(evidence or []),
            dependencies=list(dependencies or []),
            metadata=dict(metadata or {}),
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
            if status == "PROVEN" and not merged_metadata.get("formal_verified"):
                raise ValueError("Bir LLM iddiası tek başına PROVEN olamaz; formal_verified metadata gerekli.")
            if status:
                raw["status"] = status
            if evidence:
                additions = [evidence] if isinstance(evidence, str) else evidence
                raw.setdefault("evidence", []).extend(str(x) for x in additions)
            raw["metadata"] = merged_metadata
            raw["updated_at"] = _now()
            data["events"].append({"ts": _now(), "type": "item_updated", "item_id": item_id, "status": status})
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
        self.update_item(target_id, status="FAIL", evidence=f"Counterexample {item.id}: {description}")
        return item

    def list_items(self, *, kind: str | None = None, status: str | None = None) -> list[ResearchItem]:
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
        """Selective context that never forgets rejected ideas.

        Every FAIL/DROPPED conjecture is retained as a compact tombstone, all
        currently live conjectures are retained in fuller form, and only audits /
        known-results are windowed by recency.
        """

        items = self.list_items()
        if not items:
            return "(henüz kayıtlı araştırma iddiası yok)"
        dead = [x for x in items if x.kind == "conjecture" and x.status in {"FAIL", "DROPPED"}]
        live = [x for x in items if x.kind == "conjecture" and x.status not in {"FAIL", "DROPPED"}]
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
        payload = {
            "created_at": _now(),
            "label": label,
            "note": note,
            "problem": self.frozen_problem(),
            "state": self._read_state(),
            "revision": self.revision(),
        }
        path = self.checkpoint_dir / f"{stamp}_{_safe_label(label)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
