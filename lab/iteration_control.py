
from __future__ import annotations

from pathlib import Path
from typing import Any

from lab.integrity import atomic_write_json, project_lock_is_live, read_json_tolerant
from lab.research_state import ResearchState
from lab.run_controller import default_runtime, now_iso
from lab.step_store import StepStore


RESTART_MARKER = "iteration_restart_pending.json"


def resume_iteration_limit(
    configured_iterations: int,
    completed_iterations: int,
    current_iteration: int,
    *,
    only_one_iteration: bool,
) -> int:
    """Return the worker upper iteration bound without mutating the frozen run config."""

    configured = max(1, int(configured_iterations))
    completed = max(0, int(completed_iterations))
    current = max(0, int(current_iteration))
    if not only_one_iteration or completed >= configured:
        return configured
    target = max(completed + 1, current or completed + 1)
    return min(configured, target)


def restart_iteration(project_root: str | Path, iteration: int) -> dict[str, Any]:
    """Drop one incomplete iteration item and clear only its resumable execution state."""

    root = Path(project_root)
    number = int(iteration)
    if number < 1:
        raise ValueError("iteration must be >= 1")
    if project_lock_is_live(root):
        raise RuntimeError("Canlı worker varken iterasyon yeniden başlatılamaz.")

    runtime_path = root / "runtime.json"
    raw_runtime = read_json_tolerant(runtime_path, {})
    runtime = dict(raw_runtime) if isinstance(raw_runtime, dict) else default_runtime()
    completed = int(runtime.get("completed_iterations", 0) or 0)
    if number <= completed:
        raise ValueError(f"Tur {number} zaten tamamlanmış; tamamlanan tur geri alınamaz.")

    state = ResearchState(root)
    candidates = [
        item
        for item in state.list_items(kind="conjecture")
        if int(item.metadata.get("iteration", -1) or -1) == number and item.status != "DROPPED"
    ]
    if not candidates:
        raise ValueError(f"Tur {number} için yeniden başlatılacak aktif ledger item bulunamadı.")

    item = candidates[-1]
    evidence_count = len(item.evidence)
    state.update_item(
        item.id,
        status="DROPPED",
        metadata={
            "superseded_reason": "iteration_restart",
            "superseded_at": now_iso(),
        },
    )
    cleared = StepStore(root).clear_iteration(number)
    marker = {
        "iteration": number,
        "item_id": item.id,
        "superseded_reason": "iteration_restart",
        "evidence_preserved": evidence_count,
        "cleared": cleared,
        "requested_at": now_iso(),
    }
    atomic_write_json(root / RESTART_MARKER, marker)
    runtime.update(
        {
            "status": "STOPPED",
            "current_iteration": number,
            "current_step": "iteration_restart_pending",
            "last_error": "",
            "updated_at": now_iso(),
        }
    )
    atomic_write_json(runtime_path, runtime)
    return marker


def consume_iteration_restart(project_root: str | Path) -> dict[str, Any] | None:
    path = Path(project_root) / RESTART_MARKER
    raw = read_json_tolerant(path, None)
    if not isinstance(raw, dict):
        return None
    path.unlink(missing_ok=True)
    return dict(raw)
