from __future__ import annotations

from copy import deepcopy
from typing import Any
import math
import threading
import time

from lab.directed_contract import Budget, Lane


class BudgetExhausted(RuntimeError):
    pass


class DirectedBudget:
    """Atomic upper-bound reservations, persisted before dispatch. No refund on uncertainty."""

    def __init__(self, limits: Budget, previous: dict | None = None, persist=None):
        self.limits, self.lock, self.persist = limits, threading.RLock(), persist
        self.data: dict[str, Any] = (
            deepcopy(previous)
            if previous
            else {
                "calls": 0,
                "reserved_tokens": 0,
                "reserved_cost_usd": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "provider_cost_usd": 0.0,
                "provider_cost_complete": True,
                "wall_seconds": 0.0,
                "lanes": {},
            }
        )
        self.started = time.monotonic()
        self.elapsed_before = self.data["wall_seconds"]
        # Old journals lack per-attempt evidence. Preserve their known charge, but
        # never infer that untracked calls were free or fully accounted for.
        for current in self.data["lanes"].values():
            if "attempts" not in current:
                current["legacy_unresolved_reserved_cost_usd"] = (
                    current.get("reserved_cost_usd", 0.0) if current.get("calls", 0) else 0.0
                )
                current["legacy_unresolved_calls"] = current.get("calls", 0)
                current["attempts"] = {}
        self._cost_bounds()

    def elapsed(self):
        return self.elapsed_before + time.monotonic() - self.started

    def reserve(self, lane: Lane, prompt: str):
        # Byte count is a conservative bound for supported byte-tokenizers plus message overhead.
        tokens = len(prompt.encode("utf-8")) + 1024 + lane.max_completion_tokens
        cost = (
            (tokens - lane.max_completion_tokens) * float(lane.input_price_per_million or 0)
            + lane.max_completion_tokens * float(lane.output_price_per_million or 0)
        ) / 1_000_000
        with self.lock:
            current = self.data["lanes"].setdefault(
                lane.lane_id, {"calls": 0, "reserved_tokens": 0, "reserved_cost_usd": 0.0}
            )
            checks = [
                self.data["calls"] + 1 <= self.limits.max_total_llm_calls,
                current["calls"] + 1
                <= min(lane.max_calls or self.limits.max_calls_per_lane, self.limits.max_retries_per_call + 1),
                self.data["reserved_tokens"] + tokens <= self.limits.max_total_tokens,
                current["reserved_tokens"] + tokens <= (lane.max_tokens or self.limits.max_tokens_per_lane),
                self.data["reserved_cost_usd"] + cost <= self.limits.max_total_cost_usd,
                current["reserved_cost_usd"] + cost <= (lane.max_cost_usd or self.limits.max_total_cost_usd),
                self.elapsed() < self.limits.max_wall_seconds,
            ]
            if not all(checks):
                raise BudgetExhausted("Call upper bound exceeds remaining task/lane budget")
            for entry in (current, self.data):
                entry["calls"] += 1
                entry["reserved_tokens"] += tokens
                entry["reserved_cost_usd"] += cost
            attempt_id = f"{lane.lane_id}:attempt:{current['calls']}"
            current.setdefault("attempts", {})[attempt_id] = {
                "reserved_tokens": tokens,
                "reserved_cost_usd": cost,
                "recorded": False,
                "cost_complete": False,
            }
            self.save()
            return attempt_id

    def record(self, lane_id: str, usage: dict, attempt_id: str | None = None):
        """Record one terminal usage observation. Replays cannot double-charge."""
        with self.lock:
            current = self.data["lanes"][lane_id]
            attempts = current.get("attempts", {})
            if attempt_id is None:
                attempt_id = next((key for key, item in attempts.items() if not item["recorded"]), None)
            if attempt_id not in attempts:
                raise ValueError("Usage must identify an existing unrecorded reservation")
            attempt = attempts[attempt_id]
            if attempt["recorded"]:
                if attempt["usage"] == usage:
                    return
                raise ValueError("Conflicting usage for an already recorded attempt")
            counters = {}
            for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens"):
                value = usage.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
                    raise BudgetExhausted("Invalid provider token telemetry; cost remains unresolved")
                counters[key] = int(value)
            raw_cost = usage.get("cost_usd")
            if raw_cost is not None and (
                isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float))
                or not math.isfinite(raw_cost) or raw_cost < 0
            ):
                raise BudgetExhausted("Invalid provider cost telemetry; cost remains unresolved")
            cost = float(raw_cost) if raw_cost is not None else 0.0
            attempt.update(recorded=True, cost_complete=raw_cost is not None, usage=deepcopy(usage))
            for key, value in counters.items():
                self.data[key] += value
            self.data["provider_cost_usd"] += cost
            current["last_usage"] = deepcopy(usage)
            current["provider_cost_usd"] = current.get("provider_cost_usd", 0.0) + cost
            current["actual_tokens"] = current.get("actual_tokens", 0) + counters["prompt_tokens"] + counters["completion_tokens"]
            self.save()
            if (
                not math.isfinite(self.data["provider_cost_usd"])
                or cost > attempt["reserved_cost_usd"]
                or counters["prompt_tokens"] + counters["completion_tokens"] > attempt["reserved_tokens"]
            ):
                raise BudgetExhausted("Provider usage exceeded declared reservation; remaining dispatch stopped")

    def _cost_bounds(self):
        unresolved = 0.0
        complete = True
        for current in self.data["lanes"].values():
            pending = [item for item in current.get("attempts", {}).values() if not item["cost_complete"]]
            unknown = current.get("legacy_unresolved_reserved_cost_usd", 0.0) + sum(
                item["reserved_cost_usd"] for item in pending
            )
            current["unresolved_reserved_cost_usd"] = unknown
            current["provider_cost_complete"] = not pending and not current.get("legacy_unresolved_calls", 0)
            current["provider_cost_lower_bound_usd"] = current.get("provider_cost_usd", 0.0)
            current["provider_cost_upper_bound_usd"] = current["provider_cost_lower_bound_usd"] + unknown
            unresolved += unknown
            complete = complete and current["provider_cost_complete"]
        self.data["unresolved_reserved_cost_usd"] = unresolved
        self.data["provider_cost_complete"] = complete
        self.data["provider_cost_lower_bound_usd"] = self.data["provider_cost_usd"]
        self.data["provider_cost_upper_bound_usd"] = self.data["provider_cost_usd"] + unresolved

    def save(self):
        with self.lock:
            self._cost_bounds()
            self.data["wall_seconds"] = self.elapsed()
            if self.persist:
                self.persist(deepcopy(self.data))

    def snapshot(self):
        with self.lock:
            self.save()
            return deepcopy(self.data)
