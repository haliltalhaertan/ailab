"""Versioned principal-researcher contracts; validation performs no model calls."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CLAIM_STATES = {
    "PROVED",
    "PROVED_WITH_DECLARED_STANDARD_INPUT",
    "FORMALLY_VERIFIED",
    "CERTIFIED_NUMERICAL",
    "NUMERICAL",
    "CONDITIONAL",
    "REFUTED",
    "INCONCLUSIVE",
    "OPEN",
    "BLOCKED",
    "INPUT_INTEGRITY_FAILURE",
    "INFRASTRUCTURE_FAILURE",
}
TASK_TYPES = {
    "analytic_derivation",
    "proof_review",
    "counterexample_search",
    "exact_computation",
    "numerical_experiment",
    "literature_scope_check",
    "formalization",
    "package_audit",
    "result_reproduction",
    "mixed",
}
SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"


def safe_relative(value: str) -> str:
    p = PurePosixPath(value.replace("\\", "/"))
    if not value or p.is_absolute() or ":" in value or any(x in {".", ".."} or x.startswith(".") for x in p.parts):
        raise ValueError("Only explicit, non-hidden relative paths are allowed")
    if any(
        re.search(r"(?i)(^|[_.-])(env|secret|credentials|authorization|token|private.?key)([_.-]|$)", x)
        for x in p.parts
    ):
        raise ValueError("Secret/configuration paths are forbidden")
    return p.as_posix()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class InputFile(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    required: bool = True
    mutable: Literal[False] = False
    _path = field_validator("path")(safe_relative)


class PrimaryClaim(StrictModel):
    claim_id: str = Field(pattern=SAFE_ID)
    statement: str = Field(min_length=1, max_length=30000)
    status_at_start: str = "OPEN"
    quantifiers: str = Field(min_length=1)
    definitions: list[str] = Field(min_length=1)

    @field_validator("status_at_start")
    @classmethod
    def known_status(cls, value):
        if value not in CLAIM_STATES:
            raise ValueError("Unsupported claim status")
        return value


class Scope(StrictModel):
    included: list[str] = Field(min_length=1)
    excluded: list[str] = Field(min_length=1)


class Budget(StrictModel):
    max_total_llm_calls: int = Field(ge=0, le=1000, strict=True)
    max_calls_per_lane: int = Field(ge=0, le=100, strict=True)
    max_total_tokens: int = Field(ge=0, le=10_000_000, strict=True)
    max_tokens_per_lane: int = Field(ge=0, le=1_000_000, strict=True)
    max_total_cost_usd: float = Field(ge=0, le=10000)
    max_wall_seconds: int = Field(gt=0, le=86400, strict=True)
    max_parallel_workers: int = Field(gt=0, strict=True)
    max_retries_per_call: int = Field(ge=0, le=3, strict=True)


class Lane(StrictModel):
    lane_id: str = Field(pattern=SAFE_ID)
    role: str = Field(min_length=1)
    independence_group: str = "shared"
    can_read_other_lane_outputs: bool = False
    depends_on: list[str] = Field(default_factory=list)
    visible_inputs: list[str] = Field(default_factory=lambda: ["task_contract", "definitions"])
    hidden_inputs: list[str] = Field(
        default_factory=lambda: ["producer_hidden_reasoning", "producer_system_prompt", "other_auditor_output"]
    )
    input_paths: list[str] = Field(default_factory=list)
    execution: Literal["model", "claim_check", "python_exact", "symbolic_algebra", "z3", "lean", "integrity"] = "model"
    model: str | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    max_completion_tokens: int = Field(default=4096, gt=0, le=65536, strict=True)
    max_reasoning_tokens: int | None = Field(default=None, gt=0, le=65536, strict=True)
    min_final_answer_tokens: int = Field(default=0, ge=0, le=65536, strict=True)
    max_calls: int | None = Field(default=None, gt=0, le=100, strict=True)
    max_tokens: int | None = Field(default=None, gt=0, le=1_000_000, strict=True)
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_wall_seconds: int = Field(default=120, gt=0, le=3600, strict=True)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    source_input: str | None = None
    claim_spec: dict[str, Any] | None = None
    check_scope: dict[str, Any] | None = None
    seed: int = 0

    @model_validator(mode="after")
    def boundaries(self):
        if self.max_reasoning_tokens is not None and self.reasoning_effort is not None:
            raise ValueError("Choose max_reasoning_tokens or reasoning_effort, not both")
        if (self.max_reasoning_tokens or 0) + self.min_final_answer_tokens > self.max_completion_tokens:
            raise ValueError("Reasoning cap plus final-answer allowance exceeds completion ceiling")
        if self.min_final_answer_tokens and self.max_reasoning_tokens is None and self.reasoning_effort != "none":
            raise ValueError("A final-answer allowance requires an explicit reasoning cap or reasoning_effort=none")
        if self.execution != "model" and (self.max_reasoning_tokens is not None or self.min_final_answer_tokens):
            raise ValueError("Reasoning/output budget policy applies only to model lanes")
        for p in self.input_paths:
            safe_relative(p)
        if self.source_input:
            safe_relative(self.source_input)
        if self.role == "ResearchManager":
            raise ValueError("ResearchManager is not a directed-task role")
        if self.execution == "claim_check" and self.claim_spec is None:
            raise ValueError("claim_check requires its immutable claim_spec")
        if self.execution in {"python_exact", "symbolic_algebra", "z3", "lean"} and not self.source_input:
            raise ValueError("Execution requires a declared source input")
        if self.execution == "model" and (
            not self.model or self.input_price_per_million is None or self.output_price_per_million is None
        ):
            raise ValueError("Model lanes require a pinned model and explicit price ceilings")
        return self


class AgentPlan(StrictModel):
    strategy: Literal["parallel_independent", "dag", "sequential"]
    max_parallel_workers: int = Field(gt=0, strict=True)
    lanes: list[Lane] = Field(min_length=1, max_length=32)


class ResultPolicy(StrictModel):
    allow_new_task_creation: Literal[False]
    allow_scope_expansion: Literal[False]
    allow_budget_expansion: Literal[False]
    allow_canonical_repository_write: Literal[False]
    unexpected_result_action: Literal["RECORD_AS_ESCALATION_CANDIDATE"]


class DirectedTaskContract(StrictModel):
    schema_version: Literal["1.0"]
    task_id: str = Field(pattern=SAFE_ID)
    project_id: str = Field(pattern=SAFE_ID)
    parent_task_id: str = Field(min_length=1)
    parent_state_commit: str = Field(pattern=r"^[a-fA-F0-9]{40}$")
    created_at: datetime
    created_by: str = Field(min_length=1)
    mode: Literal["directed_task"]
    objective: str = Field(min_length=1, max_length=30000)
    task_type: str
    primary_claim: PrimaryClaim
    scope: Scope
    inputs: list[InputFile]
    input_manifests: list[InputFile] = Field(default_factory=list)
    allowed_tools: list[str]
    forbidden_tools: list[str]
    allowed_methods: list[str] = Field(min_length=1)
    forbidden_methods: list[str] = Field(min_length=1)
    agent_plan: AgentPlan
    budget: Budget
    stop_rules: list[str] = Field(min_length=1)
    required_outputs: list[str] = Field(min_length=1)
    result_policy: ResultPolicy

    @model_validator(mode="after")
    def coherent(self):
        if self.created_at.tzinfo is None:
            raise ValueError("created_at requires RFC3339 timezone")
        if self.task_type not in TASK_TYPES:
            raise ValueError("Unsupported task_type")
        if set(self.allowed_tools) & set(self.forbidden_tools) or set(self.allowed_methods) & set(
            self.forbidden_methods
        ):
            raise ValueError("Allowed and forbidden capabilities overlap")
        if self.agent_plan.max_parallel_workers > self.budget.max_parallel_workers:
            raise ValueError("Plan concurrency exceeds budget")
        lanes = {x.lane_id: x for x in self.agent_plan.lanes}
        if len(lanes) != len(self.agent_plan.lanes):
            raise ValueError("Duplicate lane_id")
        paths = [x.path for x in [*self.inputs, *self.input_manifests]]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate input path")
        for lane in lanes.values():
            if lane.execution == "model" and any(value <= 0 for value in (
                self.budget.max_total_llm_calls, self.budget.max_calls_per_lane,
                self.budget.max_total_tokens, self.budget.max_tokens_per_lane,
                self.budget.max_total_cost_usd,
            )):
                raise ValueError("Model lanes require positive call, token and cost ceilings; zero is native-only")
            if lane.execution == "model" and "model" in self.forbidden_tools:
                raise ValueError("Model execution is forbidden by this contract")
            if not set(lane.depends_on) <= lanes.keys() or lane.lane_id in lane.depends_on:
                raise ValueError("Unknown/self dependency")
            if not set(lane.input_paths) <= set(paths) or (lane.source_input and lane.source_input not in paths):
                raise ValueError("Undeclared lane input")
            if lane.execution != "model" and lane.execution not in self.allowed_tools:
                raise ValueError("Undeclared tool execution")
            if lane.max_calls and lane.max_calls > self.budget.max_calls_per_lane:
                raise ValueError("Lane calls exceed ceiling")
            if lane.max_tokens and lane.max_tokens > self.budget.max_tokens_per_lane:
                raise ValueError("Lane tokens exceed ceiling")
        done = set()
        while len(done) < len(lanes):
            ready = {n for n, x in lanes.items() if n not in done and set(x.depends_on) <= done}
            if not ready:
                raise ValueError("Dependency cycle")
            done |= ready
        for value in [
            self.objective,
            self.primary_claim.statement,
            self.primary_claim.quantifiers,
            *self.primary_claim.definitions,
            *self.scope.included,
            *self.scope.excluded,
        ]:
            if not value.strip():
                raise ValueError("Empty objective/claim/scope/definition")
        for output in self.required_outputs:
            safe_relative(output)
        return self


class DirectedTaskRouter:
    @staticmethod
    def route(contract: DirectedTaskContract) -> list[dict]:
        return [lane.model_dump(mode="json") for lane in contract.agent_plan.lanes]
