from __future__ import annotations

from contextvars import ContextVar
from typing import Any


_ROLE_COMPLETION: ContextVar[dict[str, bool]] = ContextVar(
    "ailab_role_completion",
    default={},
)


def reset_completion_integrity() -> None:
    """Start a fresh completion-integrity scope for a new theorem lab."""

    _ROLE_COMPLETION.set({})


def record_role_completion(role: str, *, truncated: bool) -> None:
    """Record whether the latest response for a research role was truncated.

    Context-local state prevents concurrent worker contexts from sharing a
    completion verdict. Each theorem iteration overwrites the standard roles as
    their calls (or sealed cache entries) are consumed.
    """

    name = str(role or "").strip()
    if not name:
        return
    updated = dict(_ROLE_COMPLETION.get())
    updated[name] = bool(truncated)
    _ROLE_COMPLETION.set(updated)


def role_incomplete(role: str) -> bool:
    return bool(_ROLE_COMPLETION.get().get(str(role or "").strip(), False))


def _role_from_step_key(step_key: str) -> str | None:
    key = str(step_key or "").strip().lower()
    if not key:
        return None
    if ":proposer" in key or ":target_repair" in key:
        return "Theorist"
    if ":verifier" in key:
        return "VerificationEngineer"
    if ":critic" in key:
        return "AdversarialCritic"
    if ":manager" in key:
        return "ResearchManager"
    if key == "literature:agent":
        return "LiteratureScout"
    if "checkpoint_audit" in key or key == "final:audit":
        return "IndependentAuditor"
    return None


def record_cached_step_completion(step_key: str, payload: dict[str, Any]) -> None:
    """Restore completion state when a sealed LLM step is reused from cache."""

    if "truncated" not in payload:
        return
    role = _role_from_step_key(step_key)
    if role is not None:
        record_role_completion(role, truncated=bool(payload.get("truncated")))


def load_bearing_structured_incomplete() -> dict[str, bool]:
    """Return current theorem-role completion flags used by evidence gates."""

    return {
        "proposer": role_incomplete("Theorist"),
        "verifier": role_incomplete("VerificationEngineer"),
        "critic": role_incomplete("AdversarialCritic"),
        "manager": role_incomplete("ResearchManager"),
    }
