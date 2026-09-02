from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import atomic_write_json, content_fingerprint, read_json_tolerant


CONTRACT_VERSION = 1
PILOT_POLICIES = {"REQUIRED", "OPTIONAL", "NOT_APPLICABLE"}
TARGET_TYPES = {"PROVE", "DISPROVE", "OPTIMIZE", "COMPUTE", "DISCOVER"}
TARGET_STATUSES = {"OPEN", "CLOSED", "FAILED", "SUPERSEDED"}
SCOPE_TYPES = {"integer_range", "enum"}
FROZEN_FIELDS = (
    "problem",
    "object_model",
    "validity_definition",
    "equivalence_definition",
    "objective",
    "forbidden_claims",
    "pilot_policy",
    "evidence_policy",
    "parameters",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(value: str) -> str:
    return " ".join(str(value or "").split())


def _target_type_from_objective(objective: dict[str, Any]) -> str:
    kind = str(objective.get("type") or "discover").lower()
    return {
        "prove": "PROVE",
        "disprove": "DISPROVE",
        "minimize": "OPTIMIZE",
        "maximize": "OPTIMIZE",
        "classify": "COMPUTE",
        "discover": "DISCOVER",
    }.get(kind, "DISCOVER")


def _validate_scope(scope: dict[str, Any] | None) -> dict[str, Any] | None:
    if scope is None:
        return None
    if not isinstance(scope, dict) or not scope:
        raise ValueError("target scope must be a non-empty object or null")
    normalized: dict[str, Any] = {}
    for key, raw in scope.items():
        if not isinstance(raw, dict):
            raise ValueError(f"scope[{key!r}] must be an object")
        kind = str(raw.get("type") or "")
        if kind not in SCOPE_TYPES:
            raise ValueError(f"unsupported scope type for {key}: {kind or '<missing>'}")
        if kind == "integer_range":
            minimum = raw.get("min")
            maximum = raw.get("max")
            if not isinstance(minimum, int) or isinstance(minimum, bool):
                raise ValueError(f"scope[{key!r}].min must be an integer")
            if not isinstance(maximum, int) or isinstance(maximum, bool):
                raise ValueError(f"scope[{key!r}].max must be an integer")
            if minimum > maximum:
                raise ValueError(f"scope[{key!r}] min cannot exceed max")
            normalized[str(key)] = {"type": kind, "min": minimum, "max": maximum}
        else:
            values = raw.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError(f"scope[{key!r}].values must be a non-empty list")
            canonical = []
            for value in values:
                if isinstance(value, (dict, list)):
                    raise ValueError(f"scope[{key!r}].values must contain scalar values")
                if value not in canonical:
                    canonical.append(value)
            normalized[str(key)] = {"type": kind, "values": canonical}
    return normalized


def scope_covers(scope: dict[str, Any] | None, covered: dict[str, Any] | None) -> bool:
    scope = _validate_scope(scope)
    if scope is None:
        return False
    try:
        covered = _validate_scope(covered)
    except ValueError:
        return False
    if covered is None:
        return False
    for key, expected in scope.items():
        actual = covered.get(key)
        if not isinstance(actual, dict) or actual.get("type") != expected.get("type"):
            return False
        if expected["type"] == "integer_range":
            if int(actual["min"]) > int(expected["min"]):
                return False
            if int(actual["max"]) < int(expected["max"]):
                return False
        else:
            if not set(expected["values"]).issubset(set(actual["values"])):
                return False
    return True


@dataclass(frozen=True)
class ResearchTarget:
    id: str
    statement: str
    target_type: str
    status: str = "OPEN"
    scope: dict[str, Any] | None = None
    superseded_by: str | None = None
    closed_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def target_hash(self) -> str:
        return content_fingerprint(
            "target:v1",
            {
                "id": self.id,
                "statement": self.statement,
                "target_type": self.target_type,
            },
        )


@dataclass
class ResearchContract:
    problem: str
    object_model: str
    validity_definition: str
    equivalence_definition: str
    objective: dict[str, Any]
    pilot_policy: str = "REQUIRED"
    known_results: list[dict[str, Any]] = field(default_factory=list)
    open_targets: list[ResearchTarget] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    evidence_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "numerical": "OPEN",
            "deterministic_computation": "COMPUTATION_PASS",
            "exhaustive_computation": "COMPUTATION_PASS",
            "formal_proof": "PROVEN",
        }
    )
    research_limits: dict[str, Any] = field(default_factory=lambda: {"max_n": None, "timeout_s": None})
    parameters: dict[str, Any] = field(default_factory=dict)
    contract_version: int = CONTRACT_VERSION
    frozen: bool = False
    contract_hash: str = ""
    frozen_at: str | None = None

    PATH_NAME = "research_contract.json"

    def __post_init__(self) -> None:
        self.problem = str(self.problem or "").strip()
        self.object_model = str(self.object_model or "").strip()
        self.validity_definition = str(self.validity_definition or "").strip()
        self.equivalence_definition = str(self.equivalence_definition or "").strip()
        self.pilot_policy = str(self.pilot_policy or "REQUIRED").upper()
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported contract_version: {self.contract_version}")
        if not self.problem:
            raise ValueError("research contract problem cannot be empty")
        if self.pilot_policy not in PILOT_POLICIES:
            raise ValueError(f"invalid pilot_policy: {self.pilot_policy}")
        if not isinstance(self.objective, dict) or not str(self.objective.get("type") or "").strip():
            raise ValueError("objective.type is required")
        seen: set[str] = set()
        normalized_targets: list[ResearchTarget] = []
        for raw in self.open_targets:
            target = raw if isinstance(raw, ResearchTarget) else self._target_from_dict(raw)
            if target.id in seen:
                raise ValueError(f"duplicate target id: {target.id}")
            seen.add(target.id)
            normalized_targets.append(target)
        self.open_targets = normalized_targets
        if self.frozen:
            expected = self.compute_hash()
            if not self.contract_hash or self.contract_hash != expected:
                raise ValueError("frozen research contract hash mismatch")

    @classmethod
    def _target_from_dict(cls, raw: dict[str, Any]) -> ResearchTarget:
        if not isinstance(raw, dict):
            raise ValueError("open_targets entries must be objects")
        target_type = str(raw.get("target_type") or "").upper()
        if not target_type:
            target_type = "DISCOVER"
        if target_type not in TARGET_TYPES:
            raise ValueError(f"invalid target_type: {target_type}")
        status = str(raw.get("status") or "OPEN").upper()
        if status not in TARGET_STATUSES:
            raise ValueError(f"invalid target status: {status}")
        target_id = str(raw.get("id") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        if not target_id or not statement:
            raise ValueError("target id and statement are required")
        return ResearchTarget(
            id=target_id,
            statement=statement,
            target_type=target_type,
            status=status,
            scope=_validate_scope(raw.get("scope")),
            superseded_by=(str(raw.get("superseded_by")) if raw.get("superseded_by") else None),
            closed_by=[str(x) for x in list(raw.get("closed_by") or [])],
            metadata=dict(raw.get("metadata") or {}),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResearchContract":
        if not isinstance(raw, dict):
            raise ValueError("research contract must be an object")
        objective = dict(raw.get("objective") or {})
        default_target_type = _target_type_from_objective(objective)
        targets = []
        for item in list(raw.get("open_targets") or []):
            value = dict(item or {})
            value.setdefault("target_type", default_target_type)
            targets.append(cls._target_from_dict(value))
        return cls(
            contract_version=int(raw.get("contract_version", CONTRACT_VERSION)),
            problem=str(raw.get("problem") or ""),
            object_model=str(raw.get("object_model") or ""),
            validity_definition=str(raw.get("validity_definition") or ""),
            equivalence_definition=str(raw.get("equivalence_definition") or ""),
            objective=objective,
            pilot_policy=str(raw.get("pilot_policy") or "REQUIRED"),
            known_results=[dict(x) for x in list(raw.get("known_results") or [])],
            open_targets=targets,
            forbidden_claims=[str(x) for x in list(raw.get("forbidden_claims") or [])],
            evidence_policy=dict(raw.get("evidence_policy") or {}),
            research_limits=dict(raw.get("research_limits") or {}),
            parameters=dict(raw.get("parameters") or {}),
            frozen=bool(raw.get("frozen", False)),
            contract_hash=str(raw.get("contract_hash") or ""),
            frozen_at=(str(raw.get("frozen_at")) if raw.get("frozen_at") else None),
        )

    @classmethod
    def load(cls, root: str | Path) -> "ResearchContract":
        path = Path(root) / cls.PATH_NAME
        raw = read_json_tolerant(path, None)
        if not isinstance(raw, dict):
            raise ValueError(f"research contract missing or invalid: {path}")
        return cls.from_dict(raw)

    @classmethod
    def load_optional(cls, root: str | Path) -> "ResearchContract | None":
        path = Path(root) / cls.PATH_NAME
        if not path.exists():
            return None
        return cls.load(root)

    def frozen_payload(self) -> dict[str, Any]:
        raw = self.to_dict(include_integrity=False)
        return {key: raw[key] for key in FROZEN_FIELDS}

    def compute_hash(self) -> str:
        return content_fingerprint("contract:v1", self.frozen_payload())

    def target(self, target_id: str, *, require_open: bool = False) -> ResearchTarget:
        for target in self.open_targets:
            if target.id == target_id:
                if require_open and target.status != "OPEN":
                    raise ValueError(f"target is not OPEN: {target_id}")
                return target
        raise KeyError(target_id)

    def resolution_scope(self, target_id: str, covered: dict[str, Any] | None) -> str:
        target = self.target(target_id)
        return "TARGET_RESOLUTION" if scope_covers(target.scope, covered) else "PARTIAL"

    def to_dict(self, *, include_integrity: bool = True) -> dict[str, Any]:
        payload = {
            "contract_version": self.contract_version,
            "problem": self.problem,
            "object_model": self.object_model,
            "validity_definition": self.validity_definition,
            "equivalence_definition": self.equivalence_definition,
            "objective": self.objective,
            "pilot_policy": self.pilot_policy,
            "known_results": self.known_results,
            "open_targets": [asdict(target) for target in self.open_targets],
            "forbidden_claims": self.forbidden_claims,
            "evidence_policy": self.evidence_policy,
            "research_limits": self.research_limits,
            "parameters": self.parameters,
            "frozen": self.frozen,
        }
        if include_integrity:
            payload["contract_hash"] = self.contract_hash
            payload["frozen_at"] = self.frozen_at
        return payload

    def _assert_append_only_against(self, existing: "ResearchContract") -> None:
        if existing.frozen:
            if existing.compute_hash() != self.compute_hash():
                raise ValueError("frozen research contract fields cannot change")
        if len(self.known_results) < len(existing.known_results):
            raise ValueError("known_results is append-only")
        for index, prior in enumerate(existing.known_results):
            if self.known_results[index] != prior:
                raise ValueError("known_results existing entries cannot change")
        current = {target.id: target for target in self.open_targets}
        for prior in existing.open_targets:
            target = current.get(prior.id)
            if target is None:
                raise ValueError("open_targets is append-only")
            if target.statement != prior.statement or target.target_type != prior.target_type:
                raise ValueError("target statement and target_type are immutable")

    def save(self, root: str | Path) -> Path:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / self.PATH_NAME
        existing = self.load_optional(root)
        if existing is not None:
            self._assert_append_only_against(existing)
        if self.frozen:
            self.contract_hash = self.compute_hash()
            self.frozen_at = self.frozen_at or _now()
        atomic_write_json(path, self.to_dict())
        return path

    def freeze(self, root: str | Path, *, frozen_problem: str | None = None) -> str:
        if frozen_problem is not None and _compact(frozen_problem) != _compact(self.problem):
            raise ValueError("research contract problem does not match problem_frozen.json")
        if self.frozen:
            expected = self.compute_hash()
            if self.contract_hash != expected:
                raise ValueError("frozen research contract hash mismatch")
            self.save(root)
            return self.contract_hash
        self.frozen = True
        self.contract_hash = self.compute_hash()
        self.frozen_at = _now()
        self.save(root)
        return self.contract_hash

    def prompt_block(self) -> str:
        known = "\n".join(
            f"- {item.get('statement', '')} [{item.get('status', 'KNOWN')}] ({item.get('source', '')})"
            for item in self.known_results
        ) or "- (none)"
        targets = "\n".join(
            f"- {target.id} [{target.target_type}] {target.statement}"
            for target in self.open_targets
            if target.status == "OPEN"
        ) or "- (none)"
        forbidden = "\n".join(f"- {claim}" for claim in self.forbidden_claims) or "- (none)"
        return (
            "\n\n--- FROZEN RESEARCH CONTRACT ---\n"
            f"OBJECT MODEL:\n{self.object_model}\n\n"
            f"VALIDITY:\n{self.validity_definition}\n\n"
            f"EQUIVALENCE:\n{self.equivalence_definition}\n\n"
            f"KNOWN RESULTS:\n{known}\n\n"
            f"OPEN TARGETS:\n{targets}\n\n"
            f"FORBIDDEN CLAIMS:\n{forbidden}\n\n"
            f"PILOT POLICY: {self.pilot_policy}\n"
            "--- END FROZEN RESEARCH CONTRACT ---"
        )
