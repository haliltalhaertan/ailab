from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "experiments" / "baseline_production_agents.json"


@lru_cache(maxsize=4)
def load_token_expectations(path: str | None = None) -> dict[str, Any]:
    """Load optional observational token ranges from the production profile.

    These ranges are telemetry only. They never affect prompts, max_tokens, run
    control, retries, promotion or evidence semantics.
    """

    source = Path(path) if path else DEFAULT_PROFILE_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def expected_token_range(
    agent: str,
    model: str,
    *,
    profile: dict[str, Any] | None = None,
) -> tuple[int | None, int | None]:
    data = profile if isinstance(profile, dict) else load_token_expectations()
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, dict):
        return None, None
    raw = agents.get(str(agent))
    if not isinstance(raw, dict):
        return None, None
    configured_model = str(raw.get("model") or "").strip()
    if configured_model and configured_model != str(model or "").strip():
        return None, None
    expected = raw.get("expected_tokens")
    if not isinstance(expected, dict):
        return None, None
    try:
        minimum = int(expected["min"]) if expected.get("min") is not None else None
        maximum = int(expected["max"]) if expected.get("max") is not None else None
    except (TypeError, ValueError):
        return None, None
    if minimum is not None and minimum < 0:
        minimum = None
    if maximum is not None and maximum < 0:
        maximum = None
    if minimum is not None and maximum is not None and maximum < minimum:
        return None, None
    return minimum, maximum


def budget_snapshot(
    agent: str,
    model: str,
    total_tokens: int,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return passive cost telemetry for one completed LLM call."""

    minimum, maximum = expected_token_range(agent, model, profile=profile)
    actual = max(0, int(total_tokens or 0))
    return {
        "expected_min": minimum,
        "expected_max": maximum,
        "actual": actual,
        "over_budget": bool(maximum is not None and actual > maximum),
    }
