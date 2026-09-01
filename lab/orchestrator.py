from lab.agent import Agent
from lab.trace import Trace


class Orchestrator:
    """Ajanları farklı desenlerde çalıştıran orkestratör."""

    def __init__(self, trace: Trace):
        self.trace = trace

    def research_loop(
        self,
        problem: str,
        proposer: Agent,
        critic: Agent,
        iterations: int = 3,
        synthesizer: Agent | None = None,
    ) -> str:
        """Öneri → eleştiri → revizyon döngüsü; açık uçlu araştırma problemleri için.

        Teorisyen ilk yaklaşımı üretir, her turda Sceptik hata/karşıörnek arar,
        Teorisyen eleştirileri gidererek derinleştirir. Sonuç opsiyonel Raporcu
        tarafından yapılandırılmış araştırma raporuna dönüştürülür.
        """
        messages = [{
            "role": "user",
            "content": (
                f"Problem:\n{problem}\n\n"
                "Bu probleme ilk çözüm yaklaşımını geliştir: ana fikir, ispat taslağı, "
                "sözde-kod, küçük örnekler ve açık sorular."
            ),
        }]
        proposal, resp = proposer.respond(messages)
        self.trace.agent_call(proposer.name, resp.model, proposer.temperature, messages, resp)

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
            critique, resp = critic.respond(messages)
            self.trace.agent_call(critic.name, resp.model, critic.temperature, messages, resp)
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
            proposal, resp = proposer.respond(messages)
            self.trace.agent_call(proposer.name, resp.model, proposer.temperature, messages, resp)

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
        report, resp = synthesizer.respond(messages)
        self.trace.agent_call(synthesizer.name, resp.model, synthesizer.temperature, messages, resp)
        return report

    def pipeline(self, task: str, agents: list[Agent]) -> str:
        """Her ajan öncekinin çıktısını alıp işler (zincir)."""
        context = [{"role": "user", "content": task}]
        current = task
        for agent in agents:
            content, resp = agent.respond(context)
            self.trace.agent_call(agent.name, resp.model, agent.temperature, context, resp)
            current = content
            context = [{"role": "user", "content": f"Görev: {task}\n\nÖnceki ajan ({agent.name}) çıktısı:\n{content}"}]
        return current

    def debate(self, topic: str, agents: list[Agent], rounds: int = 2, judge: Agent | None = None) -> str:
        """Ajanlar sırayla birbirinin argümanına yanıt verir; opsiyonel hakem karar verir."""
        transcript: list[dict] = []
        for r in range(rounds):
            for agent in agents:
                visible = "\n\n".join(f"[{m['speaker']}]: {m['text']}" for m in transcript) or "(henüz konuşma yok, seni başlatıyorum)"
                prompt = f"Konu: {topic}\n\nŞu ana kadarki tartışma:\n{visible}\n\nSıra sende ({agent.name}). Argümanını yaz."
                messages = [{"role": "user", "content": prompt}]
                content, resp = agent.respond(messages)
                self.trace.agent_call(agent.name, resp.model, agent.temperature, messages, resp)
                transcript.append({"speaker": agent.name, "round": r + 1, "text": content})
        if judge is None:
            return "\n\n".join(f"[{m['speaker']} | tur {m['round']}]: {m['text']}" for m in transcript)
        visible = "\n\n".join(f"[{m['speaker']}]: {m['text']}" for m in transcript)
        prompt = f"Konu: {topic}\n\nTartışma:\n{visible}\n\nHakem olarak kazanan argümanı ve gerekçesini belirt."
        messages = [{"role": "user", "content": prompt}]
        verdict, resp = judge.respond(messages)
        self.trace.agent_call(judge.name, resp.model, judge.temperature, messages, resp)
        return verdict

    def panel(self, question: str, agents: list[Agent], synthesizer: Agent | None = None) -> str:
        """Tüm ajanlar bağımsız cevap verir; sentezleyici bunları birleştirir."""
        answers: list[tuple[str, str]] = []
        for agent in agents:
            messages = [{"role": "user", "content": question}]
            content, resp = agent.respond(messages)
            self.trace.agent_call(agent.name, resp.model, agent.temperature, messages, resp)
            answers.append((agent.name, content))
        if synthesizer is None:
            return "\n\n".join(f"### {n}\n{a}" for n, a in answers)
        visible = "\n\n".join(f"[{n}]:\n{a}" for n, a in answers)
        prompt = f"Soru: {question}\n\nPanelist cevapları:\n{visible}\n\nBu cevapları tek bir tutarlı yanıtla birleştir."
        messages = [{"role": "user", "content": prompt}]
        result, resp = synthesizer.respond(messages)
        self.trace.agent_call(synthesizer.name, resp.model, synthesizer.temperature, messages, resp)
        return result
