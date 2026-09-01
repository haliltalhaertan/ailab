from __future__ import annotations

import hashlib
import os
from typing import Any

from lab.integrity import content_fingerprint
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused, ResearchStopped, now_iso
from lab.theorem_engine import TheoremResearchLab as CoreTheoremResearchLab
from lab.tools import LeanTool, ResearchToolbox, ToolResult


class TheoremResearchLab(CoreTheoremResearchLab):
    """Integrity wrapper around the single production theorem engine.

    It keeps the core workflow in ``theorem_engine.py`` while enforcing the
    invariants that need project/iteration context: project-local formal files,
    early project locking, same-iteration Lean draft/check binding, and generic
    exception runtime recovery.
    """

    def __init__(
        self,
        trace,
        state: ResearchState,
        literature=None,
        toolbox: ResearchToolbox | None = None,
        **kwargs: Any,
    ):
        if toolbox is None:
            toolbox = ResearchToolbox(lean_root=state.root / "formal")
        elif isinstance(getattr(toolbox, "lean", None), LeanTool):
            timeout_s = int(getattr(toolbox.lean, "timeout_s", 120))
            toolbox.lean = LeanTool(state.root / "formal", timeout_s=timeout_s)
        super().__init__(trace, state, literature=literature, toolbox=toolbox, **kwargs)
        self._active_iteration: int | None = None
        self._active_item_id = ""
        self._active_claim_sha256 = ""

    def _ensure_item_matches_proposal(self, iteration: int, proposal: dict[str, Any], snapshot: dict[str, Any]):
        item = super()._ensure_item_matches_proposal(iteration, proposal, snapshot)
        self._active_iteration = int(iteration)
        self._active_item_id = item.id
        self._active_claim_sha256 = hashlib.sha256(item.claim.encode("utf-8")).hexdigest()
        return item

    def _formal_tool(self, request: dict[str, Any], step_key: str) -> ToolResult:
        if self._active_iteration is None or not self._active_item_id:
            return ToolResult(False, "lean", error="Formal tool current iteration/item binding olmadan çalışamaz.")

        theorem_name = str(request.get("theorem_name") or "").strip()
        theorem_type = str(request.get("theorem_type") or "").strip()
        source = str(request.get("source") or "")
        filename = f"iter-{self._active_iteration}-{self._active_item_id}.lean"
        enriched = {
            "tool": "lean_draft",
            "file": filename,
            "source": source,
            "theorem_name": theorem_name,
            "theorem_type": theorem_type,
            "item_id": self._active_item_id,
            "iteration": self._active_iteration,
            "claim_sha256": self._active_claim_sha256,
        }
        fingerprint = content_fingerprint("bound_formal_tool:v1", enriched)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw = cached.get("result")
            if isinstance(raw, dict):
                self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                return ToolResult(
                    bool(raw.get("ok")),
                    str(raw.get("tool") or "lean"),
                    str(raw.get("output") or ""),
                    str(raw.get("error") or ""),
                    dict(raw.get("metadata") or {}),
                )

        self._check_stop()
        self._set_runtime(current_step=step_key)
        self.trace.log("tool_start", request={k: v for k, v in enriched.items() if k != "source"}, step_key=step_key)
        draft = self.toolbox.lean.draft_source(
            filename,
            source,
            theorem_name=theorem_name,
            theorem_type=theorem_type,
            item_id=self._active_item_id,
            iteration=self._active_iteration,
            claim_sha256=self._active_claim_sha256,
        )
        if not draft.ok:
            result = draft
        else:
            dmeta = dict(draft.metadata or {})
            result = self.toolbox.lean.check_file(
                filename,
                expected_sha256=str(dmeta.get("lean_sha256") or ""),
                expected_item_id=self._active_item_id,
                expected_iteration=self._active_iteration,
                expected_claim_sha256=self._active_claim_sha256,
                expected_theorem_name=theorem_name,
                expected_theorem_type=theorem_type,
            )
            merged = dict(dmeta)
            merged.update(result.metadata or {})
            merged["draft_checked_same_step"] = True
            result.metadata = merged
        self.trace.log("tool_result", step_key=step_key, **result.as_dict())
        self._cache_put(
            step_key,
            {
                "status": "COMPLETE",
                "fingerprint": fingerprint,
                "result": result.as_dict(),
                "completed_at": now_iso(),
            },
        )
        return result

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        name = str((request or {}).get("tool") or "none").strip().lower()
        if name == "lean":
            result = ToolResult(
                False,
                "lean",
                error="Direct lean check disabled. Use lean_draft; the engine checks the exact bound draft automatically.",
            )
            self.trace.log("tool_result", step_key=step_key, **result.as_dict())
            return result
        if name == "lean_draft":
            return self._formal_tool(dict(request or {}), step_key)
        return super()._tool(request, step_key)

    def run(self, *args: Any, **kwargs: Any) -> str:
        # Acquire before _save_config(), clear_stale_stop(), or any theorem-state
        # mutation in the core run method. ProjectRunLock is re-entrant for this
        # exact lock instance, so the core ``with self.controller.lock`` remains.
        try:
            with self.controller.lock:
                self.trace.log(
                    "project_lock_preflight_acquired",
                    project_root=str(self.state.root),
                    pid=os.getpid(),
                )
                return super().run(*args, **kwargs)
        except (ResearchStopped, ResearchPaused):
            raise
        except Exception as exc:
            try:
                self._set_runtime(status="PAUSED_ERROR", last_error=repr(exc))
            except Exception:
                pass
            self.trace.log("run_unhandled_error", error=repr(exc))
            raise
