from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTINGS_PATH = Path("code_experiment_settings.json")
DEFAULTS: dict[str, Any] = {
    "model": "",
    "max_steps": 8,
    "timeout_s": 60,
    "memory_limit_mb": 768,
    "max_output_mb": 4,
}


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except Exception:
        return default


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
    merged["max_steps"] = _bounded_int(merged.get("max_steps"), 8, 1, 20)
    merged["timeout_s"] = _bounded_int(merged.get("timeout_s"), 60, 5, 300)
    merged["memory_limit_mb"] = _bounded_int(merged.get("memory_limit_mb"), 768, 128, 8192)
    merged["max_output_mb"] = _bounded_int(merged.get("max_output_mb"), 4, 1, 64)
    merged["model"] = str(merged.get("model") or "").strip()
    return merged


def save_code_experiment_settings(
    settings: dict[str, Any], path: str | Path = SETTINGS_PATH
) -> Path:
    target = Path(path)
    value = load_code_experiment_settings(path)
    value.update(settings)
    value["model"] = str(value.get("model") or "").strip()
    value["max_steps"] = _bounded_int(value.get("max_steps"), 8, 1, 20)
    value["timeout_s"] = _bounded_int(value.get("timeout_s"), 60, 5, 300)
    value["memory_limit_mb"] = _bounded_int(value.get("memory_limit_mb"), 768, 128, 8192)
    value["max_output_mb"] = _bounded_int(value.get("max_output_mb"), 4, 1, 64)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
