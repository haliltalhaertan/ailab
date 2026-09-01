import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


class Trace:
    def __init__(self, experiment: str, out_dir: str | Path = "runs"):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(out_dir) / f"{stamp}_{experiment}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"

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
        self.log(
            "llm_call",
            agent=agent,
            model=model,
            temperature=temperature,
            messages=messages,
            output=response.content,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_s=response.latency_s,
        )

    def close(self) -> Path:
        totals: dict[str, dict] = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_s": 0.0})
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                ev = json.loads(line)
                if ev.get("type") != "llm_call":
                    continue
                t = totals[ev["agent"]]
                t["calls"] += 1
                t["prompt_tokens"] += ev["prompt_tokens"]
                t["completion_tokens"] += ev["completion_tokens"]
                t["latency_s"] += ev["latency_s"]
        summary = {
            "agents": dict(totals),
            "total_calls": sum(t["calls"] for t in totals.values()),
            "total_tokens": sum(t["prompt_tokens"] + t["completion_tokens"] for t in totals.values()),
        }
        out = self.run_dir / "summary.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
