from __future__ import annotations

from typing import Any, Callable

from lab.tools import ResearchToolbox, ToolResult


ToolHandler = Callable[[dict[str, Any]], ToolResult | None]


class ToolRegistry:
    """Single source of truth for theorem tool names, schema and dispatch.

    Direct ``lean`` checks are intentionally not exposed to LLMs. Formal work
    starts with ``lean_draft``; the theorem engine binds that exact source to the
    current ledger item and immediately checks the same SHA.
    """

    BUILTIN_NAMES = ("script", "z3", "lean_draft", "tropical_grid")

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

    def names(self) -> tuple[str, ...]:
        return ("none",) + tuple(sorted(self._handlers))

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
        if name == "lean":
            return ToolResult(
                False,
                "lean",
                error="Direct lean check is disabled; request lean_draft and let the engine bind/check it.",
            )
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(
                False,
                name,
                error=f"Bilinmeyen tool: {name}. Geçerli tool'lar: {self.schema_string()}",
            )
        return handler(request)
