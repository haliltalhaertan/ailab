import os
import re
import sys

from dotenv import load_dotenv

from lab import Agent, ResearchState, ResearchToolbox, TheoremResearchLab, Trace

load_dotenv()

DEFAULT_PROBLEM = """Let P_n be the simple s-t path provenance polynomial of K_n over the
min-plus tropical semiring. Improve either the known O(n^3) circuit upper bound
or the trivial Omega(n^2) lower bound, or isolate a new rigorous barrier/subclass result."""


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return value[:60] or "research"


def model(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default)


def main() -> None:
    problem = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROBLEM
    project_id = slugify(sys.argv[2] if len(sys.argv) > 2 else "tropical-circuit")
    iterations = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    manager = Agent(
        "ResearchManager",
        "Sen araştırma yöneticisisin. Yeni fikir üretmekten çok doğru dalı seç, FAIL fikirleri kapalı tut, "
        "kanıt standardını koru ve her tur tek bir kesin sonraki görev ver.",
        model=model("LAB_MANAGER_MODEL", "openai/gpt-4o"),
        temperature=0.2,
    )
    proposer = Agent(
        "Theorist",
        "Sen yaratıcı bir teorik bilgisayar bilimcisin. Küçük, test edilebilir lemma/construction üret. "
        "Bilinen sonucu yeniden keşfetmekten kaçın; varsayımı açıkça etiketle.",
        model=model("LAB_PROPOSER_MODEL", "deepseek/deepseek-r1"),
        temperature=0.8,
    )
    critic = Agent(
        "AdversarialCritic",
        "Sen bağımsız adversarial matematik hakemisin. Önceliğin çözümü geliştirmek değil çürütmektir: "
        "karşıörnek, gizli varsayım, yanlış model ve asymptotic hata ara.",
        model=model("LAB_CRITIC_MODEL", "anthropic/claude-3.5-sonnet"),
        temperature=0.2,
    )
    verifier = Agent(
        "VerificationEngineer",
        "Sen doğrulama mühendisisin. LLM kanaatini ispat sayma. Hangi deterministic test, Z3 sorgusu, "
        "küçük-n enumeration veya formal proof gerektiğini belirt.",
        model=model("LAB_VERIFIER_MODEL", "openai/gpt-4o"),
        temperature=0.1,
    )
    literature_agent = Agent(
        "LiteratureScout",
        "Sen novelty/literatür tarama ajanısın. Sadece verilen bibliyografik adaylar hakkında emin olduğun kadar konuş; "
        "kanıt veya theorem içeriği uydurma. Benzer sonuçları ve aranması gereken terimleri işaretle.",
        model=model("LAB_LITERATURE_MODEL", "openai/gpt-4o-mini"),
        temperature=0.1,
    )
    auditor = Agent(
        "IndependentAuditor",
        "Sen araştırma ekibinden bağımsız sıfır-güven denetçisisin. Kanıt yükünü yüksek tut; "
        "OPEN ile PROVEN'i kesin ayır ve novelty risklerini işaretle.",
        model=model("LAB_AUDITOR_MODEL", "google/gemini-2.0-flash-001"),
        temperature=0.1,
    )

    trace = Trace(f"theorem_{project_id}")
    state = ResearchState(f"research_state/{project_id}")
    lab = TheoremResearchLab(trace, state, toolbox=ResearchToolbox())
    try:
        report = lab.run(
            problem,
            manager=manager,
            proposer=proposer,
            critic=critic,
            verifier=verifier,
            auditor=auditor,
            literature_agent=literature_agent,
            iterations=iterations,
            literature_query=os.environ.get("LAB_LITERATURE_QUERY"),
            checkpoint_every=2,
        )
    finally:
        trace.close()

    print(report)
    print(f"\nResearch state: {state.root}")
    print(f"Trace: {trace.run_dir}")


if __name__ == "__main__":
    main()
