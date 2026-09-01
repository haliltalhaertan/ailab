from dataclasses import dataclass, field
from typing import Any, Callable

from lab.client import LLMClient, LLMResponse


@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    client: LLMClient = field(default_factory=LLMClient)

    def respond(
        self,
        messages: list[dict],
        stream_callback: Callable[[str, Any], None] | None = None,
    ) -> tuple[str, LLMResponse]:
        full = [{"role": "system", "content": self.system_prompt}] + messages
        resp = self.client.complete(
            full,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream_callback=stream_callback,
        )
        return resp.content, resp
