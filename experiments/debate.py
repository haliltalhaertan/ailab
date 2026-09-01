import sys

from dotenv import load_dotenv

from lab import Agent, Orchestrator, Trace

load_dotenv()


def main():
    topic = sys.argv[1] if len(sys.argv) > 1 else "Yapay zeka geliştirmeyi açık kaynak mı olmalı yoksa kapalı laboratuvarlarda mı yürütmeli?"

    pro = Agent(
        name="Taraftar_A",
        system_prompt="Sen 'A' pozisyonunun tutkulu bir savunucususun. En güçlü argümanlarını, verilerle destekle.",
        temperature=0.9,
    )
    con = Agent(
        name="Taraftar_B",
        system_prompt="Sen 'B' pozisyonunun tutkulu bir savunucususun. Rakibin argümanlarını çürüt, kendi pozisyonunu güçlendir.",
        temperature=0.9,
    )
    judge = Agent(
        name="Hakem",
        system_prompt="Sen tarafsız bir hakemsin. Tartışmayı objektif kriterlerle değerlendirip kazananı ve gerekçesini açıkla.",
        temperature=0.3,
    )

    trace = Trace("debate")
    orch = Orchestrator(trace)
    verdict = orch.debate(topic, [pro, con], rounds=2, judge=judge)
    trace.close()

    print("\n" + "=" * 60)
    print("HAKEM KARARI")
    print("=" * 60)
    print(verdict)
    print(f"\nTrace kaydedildi: {trace.run_dir}")


if __name__ == "__main__":
    main()
