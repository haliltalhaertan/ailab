from __future__ import annotations

import importlib.util
import json
import os
import shutil
from typing import Any, Callable

from lab.tools import ResearchToolbox, ToolResult


ToolHandler = Callable[[dict[str, Any]], ToolResult | None]
ToolAvailability = dict[str, dict[str, Any]]
EFFECTIVE_AVAILABILITY_ENV = "AILAB_EFFECTIVE_TOOL_AVAILABILITY"


class ToolRegistry:
    """Single source of truth for theorem tool names, availability and dispatch.

    Direct ``lean`` checks are intentionally not exposed to LLMs. Formal work
    starts with ``lean_draft``; the theorem engine binds that exact source to the
    current ledger item and immediately checks the same SHA.

    Availability controls what an LLM is *offered*. Dispatch also checks the
    effective snapshot so a hallucinated/old tool request still fails closed.
    """

    BUILTIN_NAMES = ("script", "z3", "lean_draft", "tropical_grid")

    def __init__(self, toolbox: ResearchToolbox | None = None):
        self.toolbox = toolbox or ResearchToolbox()
        self._handlers: dict[str, ToolHandler] = {name: self._builtin for name in self.BUILTIN_NAMES}
        self._effective_availability: ToolAvailability | None = None
        raw = str(os.environ.get(EFFECTIVE_AVAILABILITY_ENV) or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                self.set_effective_availability(parsed)

    def register(self, name: str, handler: ToolHandler) -> None:
        key = str(name).strip().lower()
        if not key or key == "none":
            raise ValueError("tool name invalid")
        self._handlers[key] = handler

    @staticmethod
    def _row(available: bool, reason: str) -> dict[str, Any]:
        return {"available": bool(available), "reason": str(reason)}

    def availability(self) -> ToolAvailability:
        """Measure the tool capabilities that are executable *right now*."""

        rows: ToolAvailability = {}
        for name in sorted(self._handlers):
            if name == "lean_draft":
                if os.environ.get("LAB_ALLOW_HOST_LEAN", "0") != "1":
                    rows[name] = self._row(False, "LAB_ALLOW_HOST_LEAN kapalı")
                else:
                    binary = shutil.which("lake") or shutil.which("lean")
                    rows[name] = self._row(bool(binary), "lean/lake PATH'te bulundu" if binary else "lean/lake PATH'te yok")
            elif name == "z3":
                available = importlib.util.find_spec("z3") is not None
                rows[name] = self._row(available, "z3-solver kullanılabilir" if available else "z3-solver kurulu değil")
            elif name == "script":
                roots = tuple(getattr(self.toolbox.scripts, "trusted_roots", ()))
                available = any(root.is_dir() for root in roots)
                rows[name] = self._row(available, "trusted script root mevcut" if available else "trusted script root bulunamadı")
            elif name == "tropical_grid":
                rows[name] = self._row(True, "yerleşik deterministic checker")
            else:
                rows[name] = self._row(True, "registered handler")
        return rows

    def set_effective_availability(self, snapshot: ToolAvailability | None) -> None:
        """Bind a run-scoped effective capability snapshot.

        ``None`` returns to measuring the current environment. A snapshot may
        only restrict dispatch/prompt exposure; unknown tools remain unavailable.
        """

        if snapshot is None:
            self._effective_availability = None
            return
        cleaned: ToolAvailability = {}
        for name, raw in snapshot.items():
            key = str(name).strip().lower()
            if key not in self._handlers or not isinstance(raw, dict):
                continue
            cleaned[key] = self._row(bool(raw.get("available")), str(raw.get("reason") or ""))
        self._effective_availability = cleaned

    def effective_availability(self) -> ToolAvailability:
        measured = self.availability()
        if self._effective_availability is None:
            return measured
        rows: ToolAvailability = {}
        for name in sorted(self._handlers):
            frozen = self._effective_availability.get(name)
            if frozen is None:
                rows[name] = self._row(False, "run capability snapshot'ında yok")
                continue
            runtime = measured.get(name, self._row(False, "runtime capability bilinmiyor"))
            if not bool(frozen.get("available")):
                rows[name] = self._row(False, str(frozen.get("reason") or "run başında kapalı"))
            elif not bool(runtime.get("available")):
                rows[name] = self._row(False, str(runtime.get("reason") or "runtime'da kullanılamıyor"))
            else:
                rows[name] = self._row(True, str(runtime.get("reason") or frozen.get("reason") or "available"))
        return rows

    def is_available(self, name: str) -> bool:
        key = str(name).strip().lower()
        row = self.effective_availability().get(key)
        return bool(row and row.get("available"))

    def unavailable_reason(self, name: str) -> str:
        key = str(name).strip().lower()
        row = self.effective_availability().get(key) or {}
        return str(row.get("reason") or "tool bu koşuda kullanılamıyor")

    def names(self, *, available_only: bool = False) -> tuple[str, ...]:
        names = sorted(self._handlers)
        if available_only:
            names = [name for name in names if self.is_available(name)]
        return ("none",) + tuple(names)

    def schema_string(self, *, available_only: bool = False) -> str:
        return "|".join(self.names(available_only=available_only))

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
                error=f"Bilinmeyen tool: {name}. Geçerli tool'lar: {self.schema_string(available_only=True)}",
            )
        if not self.is_available(name):
            return ToolResult(
                False,
                name,
                error=f"Tool bu koşuda kullanılamıyor: {self.unavailable_reason(name)}",
                metadata={"tool_unavailable": True, "availability_reason": self.unavailable_reason(name)},
            )
        return handler(request)
