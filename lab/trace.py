import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


class Trace:
    def __init__(self, experiment: str, out_dir: str | Path = "runs"):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(out_dir) / f"{stamp}_{experiment}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_perf = time.perf_counter()

    def log(self, event_type: str, **data) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def agent_call(
        self,
        agent: str,
        model: str,
        temperature: float,
        messages: list[dict],
        response,
    ) -> None:
        exact_messages = getattr(response, "request_messages", None) or messages
        self.log(
            "llm_call",
            agent=agent,
            model=model,
            temperature=temperature,
            messages=exact_messages,
            output=response.content,
            provider_reasoning=getattr(response, "provider_reasoning", ""),
            reasoning_details=getattr(response, "reasoning_details", None),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            reasoning_tokens=getattr(response, "reasoning_tokens", 0),
            cached_tokens=getattr(response, "cached_tokens", 0),
            total_tokens=response.prompt_tokens + response.completion_tokens,
            cost_usd=getattr(response, "cost_usd", None),
            latency_s=response.latency_s,
        )

    def close(self) -> Path:
        def new_total() -> dict:
            return {
                "calls": 0,
                "models": [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cost_available_calls": 0,
                "latency_s": 0.0,
            }

        totals: dict[str, dict] = defaultdict(new_total)
        all_events: list[dict] = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    ev = json.loads(line)
                    all_events.append(ev)
                    if ev.get("type") != "llm_call":
                        continue
                    t = totals[ev["agent"]]
                    t["calls"] += 1
                    if ev.get("model") and ev["model"] not in t["models"]:
                        t["models"].append(ev["model"])
                    t["prompt_tokens"] += int(ev.get("prompt_tokens", 0) or 0)
                    t["completion_tokens"] += int(ev.get("completion_tokens", 0) or 0)
                    t["reasoning_tokens"] += int(ev.get("reasoning_tokens", 0) or 0)
                    t["cached_tokens"] += int(ev.get("cached_tokens", 0) or 0)
                    t["total_tokens"] += int(
                        ev.get(
                            "total_tokens",
                            int(ev.get("prompt_tokens", 0) or 0)
                            + int(ev.get("completion_tokens", 0) or 0),
                        )
                        or 0
                    )
                    if ev.get("cost_usd") is not None:
                        t["cost_usd"] += float(ev["cost_usd"])
                        t["cost_available_calls"] += 1
                    t["latency_s"] += float(ev.get("latency_s", 0.0) or 0.0)

        agents = dict(totals)
        total_calls = sum(t["calls"] for t in agents.values())
        cost_available_calls = sum(t["cost_available_calls"] for t in agents.values())
        total_prompt = sum(t["prompt_tokens"] for t in agents.values())
        total_completion = sum(t["completion_tokens"] for t in agents.values())
        total_reasoning = sum(t["reasoning_tokens"] for t in agents.values())
        total_cached = sum(t["cached_tokens"] for t in agents.values())
        total_tokens = sum(t["total_tokens"] for t in agents.values())
        total_cost = sum(t["cost_usd"] for t in agents.values())
        llm_latency = sum(t["latency_s"] for t in agents.values())
        finished_at = datetime.now(timezone.utc).isoformat()
        wall_time_s = round(time.perf_counter() - self._started_perf, 3)

        for t in agents.values():
            t["cost_usd"] = round(t["cost_usd"], 8)
            t["latency_s"] = round(t["latency_s"], 3)
            t["cost_complete"] = t["cost_available_calls"] == t["calls"]

        summary = {
            "started_at": self.started_at,
            "finished_at": finished_at,
            "wall_time_s": wall_time_s,
            "llm_latency_s": round(llm_latency, 3),
            "agents": agents,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_reasoning_tokens": total_reasoning,
            "total_cached_tokens": total_cached,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 8),
            "cost_available_calls": cost_available_calls,
            "cost_complete": cost_available_calls == total_calls if total_calls else True,
            "event_count": len(all_events),
        }
        out = self.run_dir / "summary.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
