import sys

from dotenv import load_dotenv

from lab import Agent, Orchestrator, Trace

load_dotenv()


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "Uzay turizminin 2050'ye kadar ekonomik etkisini değerlendir."

    researcher = Agent(
        name="Arastirmaci",
        system_prompt="Sen bir araştırmacısın. Verilen konu hakkında olgusal, yapılandırılmış bir arka plan raporu yaz. Spekülasyon yapma, eksik noktaları belirt.",
    )
    analyst = Agent(
        name="Analist",
        system_prompt="Sen bir analistsin. Sana verilen araştırma notlarını eleştirel bir şekilde analiz et, fırsat ve riskleri listele.",
    )
    critic = Agent(
        name="Elestirmen",
        system_prompt="Sen bir eleştirmensin. Analizi denetle: zayıf varsayımları, eksik verileri ve mantık hatalarını işaretle ve son bir özet ver.",
    )

    trace = Trace("pipeline")
    orch = Orchestrator(trace)
    result = orch.pipeline(task, [researcher, analyst, critic])
    trace.close()

    print("\n" + "=" * 60)
    print("SON ÇIKTI")
    print("=" * 60)
    print(result)
    print(f"\nTrace kaydedildi: {trace.run_dir}")


if __name__ == "__main__":
    main()
