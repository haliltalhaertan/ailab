from __future__ import annotations

from typing import Any

from lab.budget import budget_snapshot
from lab.trace import Trace as _BaseTrace


class Trace(_BaseTrace):
    """Production trace enriched with completion/reasoning request policy."""

    def _auto_stage_end_for_llm_call(self, data: dict[str, Any]) -> None:
        agent = str(data.get("agent") or "Agent")
        step_key = self._auto_stage_by_agent.get(agent)
        if not step_key:
            return
        start = self._active_stages.get(step_key)
        if not start or start.get("auto") is not True:
            return
        end = {
            "method": start.get("method") or self.experiment_method or "theorem_lab",
            "label": start.get("label"),
            "index": start.get("index"),
            "total": start.get("total"),
            "total_is_minimum": bool(start.get("total_is_minimum")),
            "agent": agent,
            "step_key": step_key,
            "total_tokens": int(data.get("total_tokens", 0) or 0),
            "completion_tokens": int(data.get("completion_tokens", 0) or 0),
            "reasoning_tokens": int(data.get("reasoning_tokens", 0) or 0),
            "answer_chars": int(data.get("answer_chars", 0) or 0),
            "cost_usd": data.get("cost_usd"),
            "latency_s": float(data.get("latency_s", 0.0) or 0.0),
            "finish_reason": data.get("finish_reason"),
            "truncated": bool(data.get("truncated")),
            "requested_max_tokens": data.get("requested_max_tokens"),
            "model_max_completion_tokens": data.get("model_max_completion_tokens"),
            "max_tokens_source": data.get("max_tokens_source"),
            "catalog_source": data.get("catalog_source"),
            "reasoning_effort_requested": data.get("reasoning_effort_requested"),
            "reasoning_effort_sent": data.get("reasoning_effort_sent"),
            "effort_resolution": data.get("effort_resolution"),
            "reasoning_max_tokens_sent": data.get("reasoning_max_tokens_sent"),
            "budget": data.get("budget") or {},
            "auto": True,
        }
        self._write_stage("stage_end", end)
        self._active_stages.pop(step_key, None)
        self._auto_stage_by_agent.pop(agent, None)

    def agent_call(self, agent: str, model: str, temperature: float, messages: list[dict], response) -> None:
        exact_messages = getattr(response, "request_messages", None) or messages
        total_tokens = int(response.prompt_tokens or 0) + int(response.completion_tokens or 0)
        budget = budget_snapshot(agent, model, total_tokens)
        finish_reason = getattr(response, "finish_reason", None)
        truncated = str(finish_reason or "").lower() == "length"
        requested_effort = getattr(response, "requested_reasoning_effort", None)
        sent_effort = getattr(response, "reasoning_effort_sent", None)
        effort_resolution = getattr(response, "effort_resolution", "provider_default")
        self.log(
            "llm_call",
            agent=agent,
            model=model,
            temperature=temperature,
            reasoning_effort=requested_effort,
            reasoning_effort_requested=requested_effort,
            reasoning_effort_sent=sent_effort,
            effort_resolution=effort_resolution,
            reasoning_max_tokens_sent=getattr(response, "reasoning_max_tokens_sent", None),
            messages=exact_messages,
            output=response.content,
            answer_chars=len(str(response.content or "")),
            provider_reasoning=getattr(response, "provider_reasoning", ""),
            reasoning_details=getattr(response, "reasoning_details", None),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            reasoning_tokens=getattr(response, "reasoning_tokens", 0),
            cached_tokens=getattr(response, "cached_tokens", 0),
            total_tokens=total_tokens,
            cost_usd=getattr(response, "cost_usd", None),
            latency_s=response.latency_s,
            finish_reason=finish_reason,
            truncated=truncated,
            requested_max_tokens=getattr(response, "requested_max_tokens", None),
            model_max_completion_tokens=getattr(response, "model_max_completion_tokens", None),
            max_tokens_source=getattr(response, "max_tokens_source", "provider_default"),
            catalog_source=getattr(response, "catalog_source", "unavailable"),
            budget=budget,
        )
        if (
            requested_effort is not None
            and requested_effort != sent_effort
            and effort_resolution != "reasoning_max_tokens"
        ):
            self.log(
                "effort_coerced",
                agent=agent,
                model=model,
                requested=requested_effort,
                sent=sent_effort,
                resolution=effort_resolution,
            )
        if budget.get("over_budget"):
            self.log(
                "unusually_expensive_call",
                agent=agent,
                model=model,
                total_tokens=total_tokens,
                expected_min=budget.get("expected_min"),
                expected_max=budget.get("expected_max"),
            )
