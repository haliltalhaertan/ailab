from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lab.reasoning_settings import normalize_effort


SETTINGS_PATH = Path("code_experiment_settings.json")
DEFAULTS: dict[str, Any] = {
    "model": "",
    "reasoning_effort": None,
    "max_steps": 8,
    "timeout_s": 60,
    "memory_limit_mb": 768,
    "max_output_mb": 4,
    "pid_limit": 8,
    "cpu_limit": 1.0,
    "container_engine": "",
    "container_image": "python:3.12-slim",
}


def load_code_experiment_settings(path: str | Path = SETTINGS_PATH) -> dict[str, Any]:
    target = Path(path)
    data: dict[str, Any] = {}
    if target.exists():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}
    merged = dict(DEFAULTS)
    merged.update(data)
    for key, default, low, high in (
        ("max_steps", 8, 1, 20),
        ("timeout_s", 60, 5, 600),
        ("memory_limit_mb", 768, 128, 8192),
        ("max_output_mb", 4, 1, 64),
        ("pid_limit", 8, 1, 128),
    ):
        try:
            merged[key] = min(high, max(low, int(merged.get(key, default))))
        except Exception:
            merged[key] = default
    try:
        merged["cpu_limit"] = min(16.0, max(0.1, float(merged.get("cpu_limit", 1.0))))
    except Exception:
        merged["cpu_limit"] = 1.0
    merged["model"] = str(merged.get("model") or "").strip()
    try:
        merged["reasoning_effort"] = normalize_effort(merged.get("reasoning_effort"))
    except ValueError:
        merged["reasoning_effort"] = None
    merged["container_engine"] = str(merged.get("container_engine") or "").strip()
    merged["container_image"] = str(merged.get("container_image") or DEFAULTS["container_image"]).strip()
    return merged


def save_code_experiment_settings(settings: dict[str, Any], path: str | Path = SETTINGS_PATH) -> Path:
    target = Path(path)
    value = dict(DEFAULTS)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                value.update(existing)
        except Exception:
            pass
    value.update(settings)
    normalized = load_code_experiment_settings_from_dict(value)
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_code_experiment_settings_from_dict(value: dict[str, Any]) -> dict[str, Any]:
    # Reuse normalizer without touching the user's real settings path.
    merged = dict(DEFAULTS)
    merged.update(value)
    for key, default, low, high in (
        ("max_steps", 8, 1, 20),
        ("timeout_s", 60, 5, 600),
        ("memory_limit_mb", 768, 128, 8192),
        ("max_output_mb", 4, 1, 64),
        ("pid_limit", 8, 1, 128),
    ):
        try:
            merged[key] = min(high, max(low, int(merged.get(key, default))))
        except Exception:
            merged[key] = default
    try:
        merged["cpu_limit"] = min(16.0, max(0.1, float(merged.get("cpu_limit", 1.0))))
    except Exception:
        merged["cpu_limit"] = 1.0
    merged["model"] = str(merged.get("model") or "").strip()
    try:
        merged["reasoning_effort"] = normalize_effort(merged.get("reasoning_effort"))
    except ValueError:
        merged["reasoning_effort"] = None
    merged["container_engine"] = str(merged.get("container_engine") or "").strip()
    merged["container_image"] = str(merged.get("container_image") or DEFAULTS["container_image"]).strip()
    return merged
