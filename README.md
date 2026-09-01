# LLM Lab

Çok ajanlı LLM araştırma laboratuvarı. OpenAI uyumlu herhangi bir API sağlayıcısıyla (varsayılan: OpenRouter) çalışır.

## Kurulum

```powershell
cd llm-lab
python -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item .env.example .env   # sonra .env içine gerçek API anahtarını yaz
```

## Mimari

```
lab/
  client.py         OpenAI uyumlu istemci (OpenRouter varsayılan)
  agent.py          Agent: isim + sistem promptu + model + sıcaklık
  orchestrator.py   Çok ajanlı desenler
  trace.py          Her çağrıyı JSONL olarak kaydeden trace sistemi
experiments/        Örnek deneyler
runs/               Deney çıktıları (trace.jsonl + summary.json)
```

### Çok ajanlı desenler

| Desen | Ne yapar | Kullanım |
|-------|----------|----------|
| `pipeline` | A → B → C zinciri, her ajan öncekinin çıktısını işler | araştırma → analiz → eleştiri |
| `research_loop` | Teorisyen önerir → Sceptik hata/karşıörnek arar → revizyon; N tur | açık uçlu matematik/CS problemleri |
| `debate` | Ajanlar N tur karşılıklı tartışır, opsiyonel hakem karar verir | pozisyon testi, argüman kalitesi |
| `panel` | Tüm ajanlar bağımsız cevap verir, sentezleyici birleştirir | fikir çeşitliliği, ansambl yanıtlar |

## Web Arayüzü

```powershell
.venv\Scripts\streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır:

- **Deney sekmesi**: Sol menüden deney tipi, problem/konu ve tur sayısını seç; her ajanın sistem promptunu, modelini ve sıcaklığını arayüzden düzenle. Çalıştırınca adım adım ilerleme, token/süre metrikleri ve sonucu gösterir.
- **Geçmiş Kayıtlar sekmesi**: `runs/` altındaki tüm deneyleri listeler; ajan başına token/süre tablosu ve her çağrının çıktısını gösterir.

## Deney çalıştırma (CLI)

```powershell
.venv\Scripts\python experiments\pipeline.py "Metin üretiminin öğretim üzerindeki etkileri"
.venv\Scripts\python experiments\debate.py "Sınırsız GPA politikası iyi mi?"
.venv\Scripts\python experiments\research.py "Problemin tanımı" 3
```

Her çalıştırma `runs/<zaman_damgasası>_<deney>/` altında:
- `trace.jsonl` — her LLM çağrısının tam kaydı (mesajlar, çıktı, token, gecikme)
- `summary.json` — ajan başına çağrı/token/gecikme toplamları

## Kendi deneyini yazma

```python
from lab import Agent, Orchestrator, Trace

trace = Trace("benim_deneyim")
orch = Orchestrator(trace)

yazar = Agent(name="Yazar", system_prompt="...", model="anthropic/claude-3.5-haiku", temperature=0.9)
editör = Agent(name="Editor", system_prompt="...", model="openai/gpt-4o-mini", temperature=0.4)

sonuc = orch.pipeline("Kısa öykü yaz ve düzenle", [yazar, editör])
trace.close()
```

## Deney fikirleri

- Farklı modelleri `panel` ile karşılaştır (aynı soru, farklı sağlayıcılar)
- `debate` tur sayısının argüman kalitesine etkisini ölç
- Ajan başına `temperature` değişkenli A/B deneyleri
- `summary.json` çıktılarını toplayıp maliyet/kalite grafikleri çıkar
