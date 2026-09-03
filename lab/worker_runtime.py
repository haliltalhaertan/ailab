from __future__ import annotations

import threading
from typing import Any

from lab.run_controller import RunController


class WorkerRuntimeBridge:
    """Bridge worker stage/stop events to the common runtime cursor."""

    def __init__(self, controller: RunController, *, background_heartbeat: bool = False):
        self.controller = controller
        self._lock = threading.RLock()
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
        while not self._stop.wait(5.0):
            with self._lock:
                self.controller.heartbeat(min_interval_s=15.0)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def cancel_check(self) -> bool:
        with self._lock:
            self.controller.heartbeat(min_interval_s=15.0)
            return self.controller.stop_path.exists()

    def on_stage(self, event: dict[str, Any]) -> None:
        with self._lock:
            if event.get("type") == "stage":
                self.controller.set_runtime(
                    current_step=str(event.get("label") or event.get("step_key") or ""),
                    current_agent=str(event.get("agent") or ""),
                )
            elif event.get("type") == "stage_end":
                self.controller.set_runtime(
                    current_step=str(event.get("label") or event.get("step_key") or ""),
                    current_agent="",
                )
