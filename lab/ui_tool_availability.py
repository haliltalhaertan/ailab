from __future__ import annotations

from typing import Any


TOOL_ORDER = ("lean_draft", "z3", "script", "tropical_grid", "code_experiment")
TOOL_LABELS = {
    "lean_draft": "Lean",
    "z3": "Z3",
    "script": "Script",
    "tropical_grid": "Tropical",
    "code_experiment": "Container",
}


def _availability_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if isinstance(raw, dict):
            rows[str(name)] = dict(raw)
    return rows


def tool_availability_rows(snapshot: Any) -> list[dict[str, Any]]:
    """Return stable UI rows from a worker ``tool_availability.json`` snapshot."""

    root = snapshot if isinstance(snapshot, dict) else {}
    effective = _availability_map(root.get("effective_tool_availability"))
    declared = _availability_map(root.get("declared_tool_availability"))
    runtime = _availability_map(root.get("runtime_tool_availability"))
    names = [name for name in TOOL_ORDER if name in effective or name in declared or name in runtime]
    names.extend(sorted((set(effective) | set(declared) | set(runtime)) - set(names)))

    rows: list[dict[str, Any]] = []
    for name in names:
        row = effective.get(name) or declared.get(name) or runtime.get(name) or {}
        rows.append(
            {
                "name": name,
                "label": TOOL_LABELS.get(name, name),
                "available": bool(row.get("available")),
                "reason": str(row.get("reason") or "durum açıklaması yok"),
            }
        )
    return rows


def tool_availability_caption(snapshot: Any) -> str:
    root = snapshot if isinstance(snapshot, dict) else {}
    if not tool_availability_rows(root):
        return "İlk theorem run başlamadan araç capability snapshot'ı oluşmaz."
    if bool(root.get("resumed_snapshot")):
        return (
            "Bu aynı run'ın dondurulmuş capability snapshot'ıdır. Resume sırasında araçlar kaybolursa daralır; "
            "sonradan kurulan yeni bir araç aynı run'ı genişletmez."
        )
    return "Capability snapshot bu run başında donduruldu; effective durum declared ∩ runtime olarak uygulanır."
