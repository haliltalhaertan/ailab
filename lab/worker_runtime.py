from __future__ import annotations

import threading
from typing import Any

from lab.run_controller import RunController


class WorkerRuntimeBridge:
    """Bridge worker stage/stop events to the common runtime cursor."""

    HEARTBEAT_POLL_S = 5.0
    HEARTBEAT_MIN_INTERVAL_S = 15.0

    def __init__(self, controller: RunController, *, background_heartbeat: bool = False):
        self.controller = controller
        # Share the controller's write lock. A heartbeat and a foreground
        # theorem runtime/research-phase update are both read-modify-write cycles.
        self._lock = controller.write_lock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if background_heartbeat:
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name="ailab-worker-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(float(self.HEARTBEAT_POLL_S)):
            with self._lock:
                self.controller.heartbeat(
                    min_interval_s=float(self.HEARTBEAT_MIN_INTERVAL_S)
                )

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, float(self.HEARTBEAT_POLL_S) + 0.5))

    def set_runtime(self, **updates: Any) -> dict[str, Any]:
        """Serialize runtime writes with heartbeat read-modify-write cycles."""

        with self._lock:
            return self.controller.set_runtime(**updates)

    def cancel_check(self) -> bool:
        with self._lock:
            self.controller.heartbeat(
                min_interval_s=float(self.HEARTBEAT_MIN_INTERVAL_S)
            )
            return self.controller.stop_path.exists()

    def on_stage(self, event: dict[str, Any]) -> None:
        if event.get("type") == "stage":
            self.set_runtime(
                current_step=str(event.get("label") or event.get("step_key") or ""),
                current_agent=str(event.get("agent") or ""),
            )
        elif event.get("type") == "stage_end":
            self.set_runtime(
                current_step=str(event.get("label") or event.get("step_key") or ""),
                current_agent="",
            )
