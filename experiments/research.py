import sys

from dotenv import load_dotenv

from lab import Agent, Orchestrator, Trace

load_dotenv()

PROPOSER_MODEL = "deepseek/deepseek-r1"
CRITIC_MODEL = "anthropic/claude-3.5-sonnet"
SYNTH_MODEL = None

DEFAULT_PROBLEM = (
    "Büyük dil modellerinin ürettiği matematiksel ispatların otomatik "
    "doğrulanabilirliği için bir çerçeve tasarla: doğrulama adımları, "
    "güvenilirlik ölçütleri ve bilinen zayıf noktalarına karşı testler."
)


def main():
    problem = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROBLEM
    iterations = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    proposer = Agent(
        name="Teorisyen",
        system_prompt=(
            "Sen matematik ve bilgisayar bilimi alanında yaratıcı bir teorisyensin. "
            "Problemlere birden fazla yaklaşımla saldır; varsayımlarını açıkça belirt, "
            "ispat taslakları, sözde-kod ve küçük örnekler ver. Emin olmadığın yerleri "
            "'varsayım' veya 'açık soru' olarak etiketle."
        ),
        model=PROPOSER_MODEL,
        temperature=0.8,
    )
    critic = Agent(
        name="Sceptik",
        system_prompt=(
            "Sen acımasız bir hakemsin. Hatalı adımları, çürük ispatları ve gizli "
            "varsayımları bulmaya çalış; karşıörnekler üret, sınır durumlarını kontrol et, "
            "bilinen teoremlerle çelişkileri işaretle. Eleştirilerin numaralı ve somut olsun; "
            "nazik olma ama adil ol."
        ),
        model=CRITIC_MODEL,
        temperature=0.3,
    )
    synthesizer = Agent(
        name="Raporcu",
        system_prompt=(
            "Sen bir araştırma raporu yazarısın. Tartışmayı taraf tutmadan, verilen "
            "yapıda sade ve keskin bir rapora dönüştürürsün."
        ),
        model=SYNTH_MODEL,
        temperature=0.4,
    )

    trace = Trace("research")
    orch = Orchestrator(trace)
    report = orch.research_loop(problem, proposer, critic, iterations=iterations, synthesizer=synthesizer)
    trace.close()

    print("\n" + "=" * 60)
    print("ARAŞTIRMA RAPORU")
    print("=" * 60)
    print(report)
    print(f"\nTrace kaydedildi: {trace.run_dir}")


if __name__ == "__main__":
    main()
