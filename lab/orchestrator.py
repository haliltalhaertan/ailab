from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from lab.agent import Agent
from lab.budget import budget_snapshot
from lab.run_controller import ResearchStopped
from lab.trace import Trace


CancelCheck = Callable[[], bool]
StageCallback = Callable[[dict[str, Any]], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    """Ajanları farklı desenlerde çalıştıran, stage-aware orkestratör."""

    def __init__(
        self,
        trace: Trace,
        *,
        cancel_check: CancelCheck | None = None,
        on_stage: StageCallback | None = None,
    ):
        self.trace = trace
        self.cancel_check = cancel_check
        self.on_stage = on_stage

    def _check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise ResearchStopped("Kullanıcı durdurma isteği gönderdi.")

    def _emit_stage(self, event_type: str, payload: dict[str, Any]) -> None:
        self.trace.log(event_type, **payload)
        if self.on_stage is not None:
            self.on_stage({"type": event_type, "ts": _now(), **payload})

    def _call(
        self,
        *,
        method: str,
        label: str,
        index: int,
        total: int | None,
        step_key: str,
        agent: Agent,
        messages: list[dict],
    ) -> tuple[str, Any]:
        self._check_cancel()
        stage = {
            "method": method,
            "label": label,
            "index": int(index),
            "total": total,
            "agent": agent.name,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "step_key": step_key,
        }
        self._emit_stage("stage", stage)
        self.trace.log(
            "agent_start",
            agent=agent.name,
            model=agent.model,
            reasoning_effort=agent.reasoning_effort,
            step_key=step_key,
            system_prompt=agent.system_prompt,
            prompt=str(messages[-1].get("content") or "") if messages else "",
        )

        def stream_callback(channel: str, payload: Any) -> None:
            self.trace.log(
                "agent_stream",
                agent=agent.name,
                model=agent.model,
                reasoning_effort=agent.reasoning_effort,
                step_key=step_key,
                channel=channel,
                delta=payload,
            )
            # The streamed delta is persisted first; a stop request then aborts
            # the provider call at the earliest callback boundary.
            self._check_cancel()

        try:
            content, response = agent.respond(messages, stream_callback=stream_callback)
        except Exception as exc:
            self.trace.log(
                "agent_error",
                agent=agent.name,
                model=agent.model,
                reasoning_effort=agent.reasoning_effort,
                step_key=step_key,
                error=repr(exc),
            )
            raise
        self.trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
        total_tokens = int(getattr(response, "prompt_tokens", 0) or 0) + int(
            getattr(response, "completion_tokens", 0) or 0
        )
        finish_reason = getattr(response, "finish_reason", None)
        stage_end = {
            "method": method,
            "label": label,
            "index": int(index),
            "total": total,
            "agent": agent.name,
            "step_key": step_key,
            "total_tokens": total_tokens,
            "reasoning_tokens": int(getattr(response, "reasoning_tokens", 0) or 0),
            "cost_usd": getattr(response, "cost_usd", None),
            "latency_s": float(getattr(response, "latency_s", 0.0) or 0.0),
            "finish_reason": finish_reason,
            "truncated": str(finish_reason or "").lower() == "length",
            "requested_max_tokens": getattr(response, "requested_max_tokens", None),
            "budget": budget_snapshot(agent.name, response.model, total_tokens),
        }
        self._emit_stage("stage_end", stage_end)
        return content, response

    def research_loop(
        self,
        problem: str,
        proposer: Agent,
        critic: Agent,
        iterations: int = 3,
        synthesizer: Agent | None = None,
    ) -> str:
        """Öneri → eleştiri → revizyon döngüsü; açık uçlu araştırma problemleri için."""

        total = 1 + (2 * iterations) + (1 if synthesizer is not None else 0)
        call_index = 1
        messages = [{
            "role": "user",
            "content": (
                f"Problem:\n{problem}\n\n"
                "Bu probleme ilk çözüm yaklaşımını geliştir: ana fikir, ispat taslağı, "
                "sözde-kod, küçük örnekler ve açık sorular."
            ),
        }]
        proposal, _resp = self._call(
            method="research_loop",
            label="İlk çözüm · Teorisyen",
            index=call_index,
            total=total,
            step_key="loop:initial:proposer",
            agent=proposer,
            messages=messages,
        )

        critiques: list[str] = []
        for i in range(iterations):
            seen = "\n\n".join(f"TUR {j + 1}:\n{c}" for j, c in enumerate(critiques)) or "(yok)"
            messages = [{
                "role": "user",
                "content": (
                    f"Problem:\n{problem}\n\nMevcut çözüm:\n{proposal}\n\n"
                    f"Önceki eleştirilerin:\n{seen}\n\n"
                    "Bu çözümü taze bir gözle denetle: hatalı adımlar, gizli varsayımlar, "
                    "karşıörnekler, sınır durumları, bilinen sonuçlarla çelişkiler. "
                    "Eleştirilerini numaralı ve somut yaz."
                ),
            }]
            call_index += 1
            critique, _resp = self._call(
                method="research_loop",
                label=f"Tur {i + 1}/{iterations} · Sceptik · eleştiri",
                index=call_index,
                total=total,
                step_key=f"loop:{i + 1}:critic",
                agent=critic,
                messages=messages,
            )
            critiques.append(critique)

            messages = [{
                "role": "user",
                "content": (
                    f"Problem:\n{problem}\n\nMevcut çözümün:\n{proposal}\n\n"
                    f"Hakem eleştirileri:\n{critique}\n\n"
                    "Eleştirileri gidererek çözümü revize et ve derinleştir. "
                    "Gideremediğin noktaları açıkça 'açık soru' olarak etiketle."
                ),
            }]
            call_index += 1
            proposal, _resp = self._call(
                method="research_loop",
                label=f"Tur {i + 1}/{iterations} · Teorisyen · revizyon",
                index=call_index,
                total=total,
                step_key=f"loop:{i + 1}:revision",
                agent=proposer,
                messages=messages,
            )

        if synthesizer is None:
            return proposal
        critique_history = "\n\n".join(critiques)
        messages = [{
            "role": "user",
            "content": (
                f"Problem:\n{problem}\n\nSon çözüm taslağı:\n{proposal}\n\n"
                f"Eleştiri geçmişi:\n{critique_history}\n\n"
                "Bunları şu yapıda bir araştırma raporuna dönüştür: "
                "(1) Problem tanımı, (2) Mevcut en iyi yaklaşım, "
                "(3) Desteklenen iddialar, (4) Zayıf noktalar ve itirazlar, "
                "(5) Açık sorular ve sonraki deney adımları."
            ),
        }]
        call_index += 1
        report, _resp = self._call(
            method="research_loop",
            label="Rapor · Raporcu",
            index=call_index,
            total=total,
            step_key="loop:report",
            agent=synthesizer,
            messages=messages,
        )
        return report

    def pipeline(self, task: str, agents: list[Agent]) -> str:
        """Her ajan öncekinin çıktısını alıp işler (zincir)."""

        total = len(agents)
        context = [{"role": "user", "content": task}]
        current = task
        for i, agent in enumerate(agents, start=1):
            content, _resp = self._call(
                method="pipeline",
                label=f"Adım {i}/{total} · {agent.name}",
                index=i,
                total=total,
                step_key=f"pipeline:{i}",
                agent=agent,
                messages=context,
            )
            current = content
            context = [{
                "role": "user",
                "content": f"Görev: {task}\n\nÖnceki ajan ({agent.name}) çıktısı:\n{content}",
            }]
        return current

    def debate(
        self,
        topic: str,
        agents: list[Agent],
        rounds: int = 2,
        judge: Agent | None = None,
    ) -> str:
        """Ajanlar sırayla birbirinin argümanına yanıt verir; opsiyonel hakem karar verir."""

        total = rounds * len(agents) + (1 if judge is not None else 0)
        call_index = 0
        transcript: list[dict] = []
        for r in range(rounds):
            for agent_index, agent in enumerate(agents, start=1):
                visible = "\n\n".join(
                    f"[{m['speaker']}]: {m['text']}" for m in transcript
                ) or "(henüz konuşma yok, seni başlatıyorum)"
                prompt = (
                    f"Konu: {topic}\n\nŞu ana kadarki tartışma:\n{visible}\n\n"
                    f"Sıra sende ({agent.name}). Argümanını yaz."
                )
                messages = [{"role": "user", "content": prompt}]
                call_index += 1
                content, _resp = self._call(
                    method="debate",
                    label=f"Tur {r + 1}/{rounds} · {agent.name}",
                    index=call_index,
                    total=total,
                    step_key=f"debate:{r + 1}:{agent_index}",
                    agent=agent,
                    messages=messages,
                )
                transcript.append({"speaker": agent.name, "round": r + 1, "text": content})
        if judge is None:
            return "\n\n".join(
                f"[{m['speaker']} | tur {m['round']}]: {m['text']}" for m in transcript
            )
        visible = "\n\n".join(f"[{m['speaker']}]: {m['text']}" for m in transcript)
        prompt = f"Konu: {topic}\n\nTartışma:\n{visible}\n\nHakem olarak kazanan argümanı ve gerekçesini belirt."
        messages = [{"role": "user", "content": prompt}]
        call_index += 1
        verdict, _resp = self._call(
            method="debate",
            label="Hakem",
            index=call_index,
            total=total,
            step_key="debate:judge",
            agent=judge,
            messages=messages,
        )
        return verdict

    def panel(
        self,
        question: str,
        agents: list[Agent],
        synthesizer: Agent | None = None,
    ) -> str:
        """Tüm ajanlar bağımsız cevap verir; sentezleyici bunları birleştirir."""

        total = len(agents) + (1 if synthesizer is not None else 0)
        answers: list[tuple[str, str]] = []
        for i, agent in enumerate(agents, start=1):
            messages = [{"role": "user", "content": question}]
            content, _resp = self._call(
                method="panel",
                label=f"Panelist {i}/{len(agents)}",
                index=i,
                total=total,
                step_key=f"panel:{i}",
                agent=agent,
                messages=messages,
            )
            answers.append((agent.name, content))
        if synthesizer is None:
            return "\n\n".join(f"### {n}\n{a}" for n, a in answers)
        visible = "\n\n".join(f"[{n}]:\n{a}" for n, a in answers)
        prompt = f"Soru: {question}\n\nPanelist cevapları:\n{visible}\n\nBu cevapları tek bir tutarlı yanıtla birleştir."
        messages = [{"role": "user", "content": prompt}]
        result, _resp = self._call(
            method="panel",
            label="Sentez · Sentezleyici",
            index=total,
            total=total,
            step_key="panel:synthesis",
            agent=synthesizer,
            messages=messages,
        )
        return result
