from __future__ import annotations

from typing import Any, Callable

from lab.tools import ResearchToolbox, ToolResult


ToolHandler = Callable[[dict[str, Any]], ToolResult | None]


class ToolRegistry:
    """Single source of truth for theorem tool names, schema and dispatch."""

    BUILTIN_NAMES = ("script", "z3", "lean_draft", "lean", "tropical_grid")

    def __init__(self, toolbox: ResearchToolbox | None = None):
        self.toolbox = toolbox or ResearchToolbox()
        self._handlers: dict[str, ToolHandler] = {
            name: self._builtin for name in self.BUILTIN_NAMES
        }

    def register(self, name: str, handler: ToolHandler) -> None:
        key = str(name).strip().lower()
        if not key or key == "none":
            raise ValueError("tool name invalid")
        self._handlers[key] = handler

    def names(self, *, include_none: bool = True) -> tuple[str, ...]:
        names = tuple(sorted(self._handlers))
        return (("none",) + names) if include_none else names

    def schema_string(self) -> str:
        return "|".join(self.names())

    def _builtin(self, request: dict[str, Any]) -> ToolResult | None:
        return self.toolbox.execute(request)

    def execute(self, request: dict[str, Any] | None) -> ToolResult | None:
        if not request:
            return None
        name = str(request.get("tool") or "none").strip().lower()
        if name in {"", "none"}:
            return None
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(False, name, error=f"Bilinmeyen tool: {name}. Geçerli tool'lar: {self.schema_string()}")
        return handler(request)
