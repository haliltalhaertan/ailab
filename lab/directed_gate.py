from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from lab.directed_contract import DirectedTaskContract, safe_relative
from lab.integrity import project_lock_is_live


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def secrets() -> list[str]:
    return [
        v
        for k, v in os.environ.items()
        if len(v) >= 8 and re.search(r"(?i)(api.?key|secret|token|password|authorization)", k)
    ]


def make_redactor():
    # Snapshot the environment once per operation, never once per nested string.
    current_secrets = tuple(secrets())

    def apply(text: Any) -> str:
        value = str(text)
        for secret in current_secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)

    return apply


def redact(text: Any) -> str:
    return make_redactor()(text)


def safe_bytes(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace")
    pattern = r"""(?i)(?:api[_-]?key|password|client_secret|access_token|refresh_token|private_key)["']?\s*[=:]\s*["']?[^\s"',}]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"""
    return redact(text) == text and re.search(pattern, text) is None


def input_path(root: Path, name: str) -> Path:
    name = safe_relative(name)
    candidate = root / name
    if not candidate.resolve().is_relative_to(root.resolve()) or any(
        p.is_symlink() for p in [candidate, *candidate.parents] if p != root.parent
    ):
        raise ValueError("Input traversal/symlink rejected")
    return candidate


def verify_snapshot(contract: DirectedTaskContract, input_root: Path, expected_inputs: list | None = None) -> None:
    """Verify the immutable bytes workers consume, including absent optional inputs."""
    declared = {spec.path: spec for spec in [*contract.inputs, *contract.input_manifests]}
    expected = (
        {item["path"] for item in expected_inputs if item["status"] == "PASS"}
        if expected_inputs is not None
        else {name for name, spec in declared.items() if spec.required or (input_root / name).exists()}
    )
    actual = set()
    for p in input_root.rglob("*"):
        if p.is_symlink():
            raise ValueError("Sealed snapshot symlink")
        if p.is_file():
            actual.add(p.relative_to(input_root).as_posix())
    if actual != expected or not expected <= declared.keys():
        raise ValueError("Sealed snapshot membership changed or input missing")
    for name in expected:
        p = input_path(input_root, name)
        raw = p.read_bytes()
        if sha(raw).lower() != declared[name].sha256.lower() or not safe_bytes(raw):
            raise ValueError("Sealed snapshot changed")
    if any(spec.required and name not in expected for name, spec in declared.items()):
        raise ValueError("Required sealed snapshot input missing")


def parent_commit(root: Path) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.resolve().as_posix()}",
            "-C",
            str(root),
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise ValueError("Parent repository commit cannot be resolved")
    return result.stdout.strip()


def load_contract(path: str | Path) -> tuple[DirectedTaskContract, bytes]:
    path = Path(path)
    safe_relative(path.name)
    if path.stat().st_size > 1_000_000:
        raise ValueError("Contract exceeds 1 MB")
    raw = path.read_bytes()
    if not safe_bytes(raw):
        raise ValueError("Secret material is forbidden in a contract")
    return DirectedTaskContract.model_validate_json(raw), raw


class CapabilityAndIntegrityGate:
    def check(
        self,
        contract_path: str | Path,
        *,
        input_root: Path | None = None,
        parent_repo: Path | None = None,
        project_root: Path | None = None,
        provider: Any = None,
        check_lock: bool = True,
    ) -> dict:
        report: dict[str, Any] = {
            "gate_status": "INVALID_CONTRACT",
            "llm_calls_made": 0,
            "inputs": [],
            "capabilities": {},
            "limitations": [],
        }
        try:
            c, raw = load_contract(contract_path)
            root = (input_root or Path(contract_path).parent).resolve()
            repo = (parent_repo or root).resolve()
            report.update(contract_hash=sha(raw), task_id=c.task_id, input_root=str(root), parent_repo=str(repo))
            if parent_commit(repo).lower() != c.parent_state_commit.lower():
                report.update(gate_status="INPUT_INTEGRITY_FAILURE", error="Parent HEAD differs from frozen commit")
                return report
            declared = {x.path: x for x in [*c.inputs, *c.input_manifests]}
            for spec in declared.values():
                path = input_path(root, spec.path)
                if not path.is_file():
                    if not spec.required:
                        report["inputs"].append({"path": spec.path, "status": "OPTIONAL_MISSING"})
                        continue
                    report.update(gate_status="BLOCKED_MISSING_INPUT", error=f"Missing input: {spec.path}")
                    return report
                if path.stat().st_size > 4_000_000:
                    raise ValueError("Input exceeds 4 MB sealed-input ceiling")
                data = path.read_bytes()
                if sha(data).lower() != spec.sha256.lower() or not safe_bytes(data):
                    report.update(gate_status="INPUT_INTEGRITY_FAILURE", error=f"Input integrity failed: {spec.path}")
                    return report
                report["inputs"].append({"path": spec.path, "status": "PASS", "sha256": sha(data)})
            for manifest in c.input_manifests:
                p = input_path(root, manifest.path)
                if not p.exists():
                    continue
                for line in p.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    parts = line.split(maxsplit=1)
                    if len(parts) != 2:
                        raise ValueError("Malformed input manifest")
                    expected, name = parts[0], safe_relative(parts[1].lstrip("*"))
                    if name not in declared or declared[name].sha256.lower() != expected.lower():
                        report.update(
                            gate_status="INPUT_INTEGRITY_FAILURE",
                            error="Manifest contains undeclared or mismatched input",
                        )
                        return report
            for lane in c.agent_plan.lanes:
                for name in [*lane.input_paths, *([lane.source_input] if lane.source_input else [])]:
                    if not input_path(root, name).is_file():
                        report.update(gate_status="BLOCKED_MISSING_INPUT", error="Lane requires missing optional input")
                        return report
                if lane.execution == "claim_check":
                    from lab.batch_lab import plan

                    plan(
                        {
                            "jobs": [
                                {
                                    "id": lane.lane_id,
                                    "claim_spec": lane.claim_spec,
                                    **({"scope": lane.check_scope} if lane.check_scope else {}),
                                }
                            ]
                        }
                    )
            capabilities = {
                "claim_check": True,
                "integrity": True,
                "z3": importlib.util.find_spec("z3") is not None,
                "symbolic_algebra": importlib.util.find_spec("lab.directed_algebra") is not None,
                "lean": False,
                "python_exact": False,
            }
            if "python_exact" in c.allowed_tools:
                binary = shutil.which("docker") or shutil.which("podman")
                if binary:
                    probe = subprocess.run(
                        [binary, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                    )
                    capabilities["python_exact"] = probe.returncode == 0
            report["capabilities"] = {name: bool(capabilities.get(name)) for name in c.allowed_tools}
            required = {x.execution for x in c.agent_plan.lanes if x.execution != "model"}
            for lane in c.agent_plan.lanes:
                if lane.execution == "symbolic_algebra" and capabilities["symbolic_algebra"]:
                    from lab.directed_algebra import recipe

                    if lane.source_input is None:
                        raise ValueError("Exact-algebra lane requires a source input")
                    value = json.loads(input_path(root, lane.source_input).read_text(encoding="utf-8"))
                    if not isinstance(value, dict):
                        raise ValueError("Exact-algebra recipe must be an object")
                    variant = value.get("variant")
                    if (
                        not isinstance(variant, str)
                        or variant not in {"derive_a", "derive_b", "boundary", "audit"}
                        or value != recipe(variant)
                    ):
                        raise ValueError("Unsupported or altered exact-algebra recipe")
            missing = sorted(name for name in required if not capabilities.get(name))
            if missing:
                report.update(gate_status="BLOCKED_MISSING_TOOL", missing_tools=missing, scientific_claim_status="OPEN")
                return report
            has_model_lanes = any(x.execution == "model" for x in c.agent_plan.lanes)
            if has_model_lanes:
                if provider is None and not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")):
                    report.update(
                        gate_status="BLOCKED_PROVIDER_CONFIGURATION",
                        error="Provider credential absent from process environment; .env is not read",
                    )
                    return report
                report["output_budget_policies"] = [
                    {
                        "lane_id": lane.lane_id,
                        "model": lane.model,
                        "max_completion_tokens": lane.max_completion_tokens,
                        "max_reasoning_tokens": lane.max_reasoning_tokens,
                        "min_final_answer_tokens": lane.min_final_answer_tokens,
                        "reasoning_effort": lane.reasoning_effort,
                        "policy": (
                            "REQUESTED_NUMERIC_REASONING_CAP" if lane.max_reasoning_tokens is not None
                            else "REQUESTED_REASONING_DISABLED" if lane.reasoning_effort == "none"
                            else "EFFORT_ONLY_OR_PROVIDER_DEFAULT"
                        ),
                        "provider_support_verified": False,
                        "final_answer_guaranteed": False,
                        "automatic_finalization_call": False,
                    }
                    for lane in c.agent_plan.lanes if lane.execution == "model"
                ]
                report["provider"] = getattr(provider, "identity", "openai-compatible-v1")
                report["endpoint"] = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
            if check_lock and project_root and project_lock_is_live(project_root):
                report.update(gate_status="OUT_OF_SCOPE", error="A live coordinator already owns this project")
                return report
            report.update(
                gate_status="FEASIBLE_WITH_LIMITATIONS",
                scientific_claim_status=c.primary_claim.status_at_start,
                limitations=[
                    "Model text is not a proof certificate",
                    "Only declared inputs are exposed; no autonomous network retrieval",
                    "Budget cost ceiling uses frozen declared price ceilings; unknown usage retains full reservation",
                ],
                maximum_concurrency=min(c.agent_plan.max_parallel_workers, c.budget.max_parallel_workers),
                estimated_call_ceiling=c.budget.max_total_llm_calls if has_model_lanes else 0,
                token_ceiling=c.budget.max_total_tokens if has_model_lanes else 0,
                cost_ceiling=c.budget.max_total_cost_usd if has_model_lanes else 0,
                effective_model_call_ceiling=c.budget.max_total_llm_calls if has_model_lanes else 0,
                effective_model_token_ceiling=c.budget.max_total_tokens if has_model_lanes else 0,
                effective_model_cost_ceiling=c.budget.max_total_cost_usd if has_model_lanes else 0,
                parent_binding={
                    "commit_scope": "HEAD_ONLY",
                    "working_tree_frozen_by_commit": False,
                    "scientific_input_binding": "EXPLICIT_INPUT_FILES_AND_MANIFESTS_ONLY",
                    "execution_sources": "Separately hashed in signed run context",
                },
                native_symbolic_scope="small_b_v1 only; not a general CAS or formal kernel",
                package_verification_scope="Byte integrity and ledger/Markdown consistency; not mathematical truth",
                dag_valid=True,
                forbidden_operations=c.forbidden_tools + c.forbidden_methods,
            )
            if has_model_lanes:
                report["limitations"].append(
                    "Reasoning and final answer share the completion ceiling. Numeric reasoning caps and "
                    "reasoning_effort=none are provider requests, not verified guarantees; some routes translate "
                    "numeric caps into effort. min_final_answer_tokens is a nominal allowance, not a promised "
                    "answer length or scientific result. No automatic finalization call is made."
                )
                if any(x.execution == "model" and x.max_reasoning_tokens is None and x.reasoning_effort != "none"
                       for x in c.agent_plan.lanes):
                    report["limitations"].append(
                        "Reasoning exhaustion risk: at least one model lane uses effort-only or provider-default "
                        "reasoning, which may consume its entire completion budget before a final answer."
                    )
            return report
        except (ValueError, OSError, TypeError, subprocess.SubprocessError) as exc:
            report["error"] = redact(exc)
            return report
