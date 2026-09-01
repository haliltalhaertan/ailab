from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


# OpenRouter uses xhigh for the strongest reasoning effort. The UI calls it Max.
UI_LEVELS = ["Provider default", "None", "Minimal", "Low", "Medium", "High", "Max"]
UI_TO_API = {
    "Provider default": None,
    "None": "none",
    "Minimal": "minimal",
    "Low": "low",
    "Medium": "medium",
    "High": "high",
    "Max": "xhigh",
}
API_TO_UI = {value: key for key, value in UI_TO_API.items()}
VALID_API_LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def settings_path() -> Path:
    return Path(os.environ.get("LAB_REASONING_SETTINGS", "reasoning_settings.json"))


def normalize_effort(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw in UI_TO_API:
        return UI_TO_API[raw]
    lowered = raw.casefold()
    aliases = {
        "default": None,
        "provider default": None,
        "off": "none",
        "none": "none",
        "minimal": "minimal",
        "min": "minimal",
        "low": "low",
        "medium": "medium",
        "med": "medium",
        "high": "high",
        "max": "xhigh",
        "maximum": "xhigh",
        "xhigh": "xhigh",
    }
    if lowered in aliases:
        return aliases[lowered]
    raise ValueError(f"Geçersiz reasoning effort: {value}")


def _base_agent_name(name: str) -> str:
    return re.sub(r"\s+\d+$", "", name.strip())


def load_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or settings_path()
    if not path.exists():
        return {"agents": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"agents": {}}
    if not isinstance(raw, dict):
        return {"agents": {}}
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        raw["agents"] = {}
    return raw


def save_settings(data: dict[str, Any], path: Path | None = None) -> Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def get_reasoning_effort(agent_name: str, path: Path | None = None) -> str | None:
    data = load_settings(path)
    agents = data.get("agents", {})
    raw = agents.get(agent_name)
    if raw is None:
        raw = agents.get(_base_agent_name(agent_name))
    try:
        return normalize_effort(raw)
    except ValueError:
        return None


def set_reasoning_effort(agent_name: str, effort: str | None, path: Path | None = None) -> Path:
    data = load_settings(path)
    data.setdefault("agents", {})[agent_name] = normalize_effort(effort)
    return save_settings(data, path)
