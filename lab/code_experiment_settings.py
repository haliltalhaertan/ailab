from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTINGS_PATH = Path("code_experiment_settings.json")
DEFAULTS: dict[str, Any] = {
    "model": "",
    "max_steps": 8,
    "timeout_s": 60,
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
    try:
        merged["max_steps"] = min(20, max(1, int(merged.get("max_steps", 8))))
    except Exception:
        merged["max_steps"] = 8
    try:
        merged["timeout_s"] = min(300, max(5, int(merged.get("timeout_s", 60))))
    except Exception:
        merged["timeout_s"] = 60
    merged["model"] = str(merged.get("model") or "").strip()
    return merged


def save_code_experiment_settings(
    settings: dict[str, Any], path: str | Path = SETTINGS_PATH
) -> Path:
    target = Path(path)
    value = load_code_experiment_settings(path)
    value.update(settings)
    value["model"] = str(value.get("model") or "").strip()
    value["max_steps"] = min(20, max(1, int(value.get("max_steps", 8))))
    value["timeout_s"] = min(300, max(5, int(value.get("timeout_s", 60))))
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
