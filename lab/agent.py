from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from lab.client import LLMClient, LLMResponse, get_default_client
from lab.reasoning_settings import get_reasoning_effort, normalize_effort
from lab.trace import get_active_trace


@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    client: LLMClient | None = None

    def __post_init__(self) -> None:
        if self.reasoning_effort is None:
            self.reasoning_effort = get_reasoning_effort(self.name)
        else:
            self.reasoning_effort = normalize_effort(self.reasoning_effort)

    def _client(self) -> LLMClient:
        if self.client is None:
            self.client = get_default_client()
        return self.client

    def respond(
        self,
        messages: list[dict],
        stream_callback: Callable[[str, Any], None] | None = None,
    ) -> tuple[str, LLMResponse]:
        full = [{"role": "system", "content": self.system_prompt}] + messages
        callback = stream_callback
        if callback is None:
            trace = get_active_trace()
            if trace is not None:
                def callback(channel: str, payload: Any) -> None:
                    trace.log(
                        "agent_stream",
                        agent=self.name,
                        model=self.model,
                        reasoning_effort=self.reasoning_effort,
                        channel=channel,
                        delta=payload,
                    )

        base_kwargs = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream_callback": callback,
        }
        client = self._client()
        try:
            resp = client.complete(
                full,
                reasoning_effort=self.reasoning_effort,
                **base_kwargs,
            )
        except TypeError as exc:
            # Compatibility with injected test clients implementing the older
            # complete(...) signature.
            text = str(exc)
            if "reasoning_effort" not in text or "unexpected keyword" not in text:
                raise
            resp = client.complete(full, **base_kwargs)
        return resp.content, resp
