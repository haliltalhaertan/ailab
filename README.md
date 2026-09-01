# LLM Lab

Çok ajanlı, doğrulama ve kalıcı araştırma hafızası destekli LLM araştırma laboratuvarı. OpenAI uyumlu herhangi bir API sağlayıcısıyla çalışır (varsayılan: OpenRouter).

Bu repo iki kullanım katmanı sunar:

1. **Genel multi-agent deneyleri:** pipeline, research loop, debate, panel.
2. **Theorem Research Lab v2:** açık matematik/teorik CS problemleri için manager + teorisyen + adversarial critic + verifier + literature scout + bağımsız auditor; kalıcı conjecture ledger, deterministic compute araçları, literature screening ve checkpoint/freeze desteği.

## Kurulum

```powershell
cd ailab
python -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item .env.example .env
```

Test araçları için:

```powershell
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\pytest -q
```

`.env` içine en az bir API anahtarı koy:

```text
OPENROUTER_API_KEY=...
```

`LAB_*_MODEL` değişkenleri yalnız varsayılan model seçimleridir. Streamlit arayüzünde her agent için OpenRouter modelini ayrı seçebilirsin.

## Mimari

```text
lab/
  client.py              OpenAI uyumlu LLM istemcisi + exact usage/cost yakalama
  agent.py               Agent tanımı
  orchestrator.py        Genel multi-agent desenleri
  trace.py               JSONL trace + token/ücret/süre ölçümü
  openrouter_catalog.py  Canlı OpenRouter model kataloğu ve fiyat bilgisi

  research_state.py      Frozen problem + conjecture/lemma/counterexample ledger
  literature.py          arXiv + Crossref novelty/literature screening
  tools.py               Kontrollü compute: checked-in Python script, Z3, tropical grid, opsiyonel Lean
  theorem_lab.py         Dinamik theorem-research workflow

experiments/
  research.py            Klasik proposer↔critic research loop
  theorem_research.py    Açık matematik/CS problemleri için v2 workflow

research_tools/           Yalnızca review edilmiş deterministic hesaplama scriptleri
formal/                   Opsiyonel checked-in Lean dosyaları
research_state/           Runtime araştırma hafızası; git'e alınmaz
runs/                     LLM/tool trace'leri ve summary.json; git'e alınmaz
templates/                Frozen problem gibi araştırma şablonları
tests/                    Deterministic unit testler
```

## Theorem Research Lab v2

Amaç, LLM'leri "ispat üreten chatbot" olarak değil, kontrollü bir araştırma sistemi içinde kullanmaktır.

```text
                    Research Manager
                          |
          +---------------+---------------+
          |               |               |
       Theorist       Adversarial       Verifier
                          Critic            |
          |               |          Python/Z3/Lean
          +---------------+---------------+
                          |
                 Research State / Graph
                          |
                  Literature Scout
                          |
                 Independent Auditor
                          |
                     Checkpoint
```

### Güvenilirlik kuralı

Bir LLM'nin veya birden fazla LLM'nin "doğru görünüyor" demesi bir iddiayı `[PROVEN]` yapmaz.

`ResearchState` bir iddianın `PROVEN` durumuna yükseltilmesi için `formal_verified=true` metadata ister. Küçük-n hesaplama yalnızca `COMPUTATION_PASS`; informal ispat en fazla `PROOF_CANDIDATE` seviyesidir.

Durumlar:

```text
OPEN
COMPUTATION_PASS
PROOF_CANDIDATE
PROVEN
FAIL
KNOWN
BARRIER
DROPPED
```

Bir counterexample kaydedildiğinde hedef iddia otomatik `FAIL` olur ve ledger'da kalır; böylece başka bir agent aynı fikri daha sonra "yeni" diye yeniden açmaz.

## Araştırma hafızası

Her theorem projesinin ayrı state klasörü vardır:

```text
research_state/<project_id>/
  problem_frozen.json
  state.json
  theorem_graph.json
  checkpoints/
```

`problem_frozen.json` yanlışlıkla başka probleme geçilmesini engeller. `state.json` conjecture, lemma, experiment, audit ve counterexample kayıtlarını tutar. `theorem_graph.json` dependency edge'lerini çıkarır. Checkpoint'ler immutable snapshot olarak yazılır.

Başlangıç şablonu: `templates/problem_frozen.md`.

## Deterministic compute katmanı

`research_tools/` altındaki scriptler LLM'den bağımsız, gözden geçirilmiş hesaplama programlarıdır.

Örnek:

```powershell
.venv\Scripts\python research_tools\tropical_path_counts.py 8
```

çıktısı complete graph `K_8` için simple source-target path sayısını exact olarak verir.

`ScriptTool` güvenlik nedeniyle LLM tarafından üretilen keyfi Python kodunu çalıştırmaz. Sadece `research_tools/` altına önceden eklenmiş `.py` dosyalarını çalıştırır ve child process'e API key gibi environment secret'larını taşımaz.

Ek doğrulayıcılar:

- **Z3Tool:** SMT-LIB sorgularını deterministic olarak kontrol eder.
- **TropicalGridTool:** min-plus circuit adaylarını küçük n ve sonlu ağırlık gridlerinde exhaustive kontrol edip counterexample arar; PASS bir genel ispat değildir.
- **LeanTool:** `lean` binary kuruluysa yalnızca `formal/` altındaki checked-in `.lean` dosyalarını kontrol eder.

## Literature / novelty screening

`LiteratureClient` API key gerektirmeden arXiv ve Crossref üzerinde aday yayınları tarar. `LiteratureScout` bu kayıtları novelty riski ve aranacak anahtar kelimeler açısından yorumlar. Bu yalnızca novelty screen'dir; "arama sonucu bulamadım" yeni teorem kanıtı veya kesin novelty garantisi değildir. Kritik bir sonuçta bağımsız literatür audit'i yine gerekir.

## Theorem research çalıştırma

### Web arayüzü — önerilen

```powershell
.venv\Scripts\streamlit run app.py
```

`Deney tipi` olarak **Teorem Araştırması** seç. Arayüz OpenRouter'ın canlı text-model kataloğunu yükler. Her rol için ayrı model seçebilirsin:

- ResearchManager
- Theorist
- AdversarialCritic
- VerificationEngineer
- LiteratureScout
- IndependentAuditor

Model selectbox'ı OpenRouter model adı, slug ve güncel input/output liste fiyatını gösterir. Katalogda görünmeyen/yeni bir model kullanmak için rolün altındaki **Manuel model ID** alanına örneğin `z-ai/glm-5.3` gibi doğrudan OpenRouter slug'ı yazabilirsin.

`.env` içindeki `LAB_MANAGER_MODEL`, `LAB_PROPOSER_MODEL` vb. değerler yalnız arayüzün ilk açılıştaki varsayılan seçimleridir; arayüzde yaptığın seçim run için önceliklidir.

### CLI

Örnek tropical circuit projesi:

```powershell
.venv\Scripts\python experiments\theorem_research.py "Let P_n be the simple s-t path provenance polynomial of K_n over the min-plus tropical semiring. Improve either the O(n^3) upper bound or the Omega(n^2) lower bound." tropical-circuit 6
```

Aynı `project_id` ile tekrar çalıştırırsan ledger devam eder; farklı problem verirsen frozen-problem koruması hata üretir.

CLI için rol bazlı modelleri environment üzerinden değiştirebilirsin:

```text
LAB_MANAGER_MODEL=...
LAB_PROPOSER_MODEL=...
LAB_CRITIC_MODEL=...
LAB_VERIFIER_MODEL=...
LAB_LITERATURE_MODEL=...
LAB_AUDITOR_MODEL=...
LAB_LITERATURE_QUERY=tropical circuit reachability provenance lower bound
```

Bağımsız auditor için mümkünse proposer/critic'ten farklı model ailesi kullan.

## Kullanım, token, ücret ve süre kayıtları

Her LLM çağrısı `runs/<timestamp>_<experiment>/trace.jsonl` içine aşağıdaki alanlarla kaydedilir:

```text
agent
model
prompt_tokens
completion_tokens
reasoning_tokens
cached_tokens
total_tokens
cost_usd
latency_s
```

OpenRouter kullanılıyorsa istemci `usage.include=true` ister ve `usage.cost` alanındaki **gerçek inference maliyetini** kaydeder. Böylece fiyat listesinden sonradan yaklaşık hesap yapmak yerine OpenRouter'ın o çağrı için bildirdiği ücret kullanılır.

Her çalışma sonunda `summary.json` ayrıca şunları tutar:

```text
started_at
finished_at
wall_time_s
total_calls
total_prompt_tokens
total_completion_tokens
total_reasoning_tokens
total_cached_tokens
total_tokens
total_cost_usd
cost_complete
agents -> model/token/cost/latency toplamları
```

`wall_time_s`, yalnız LLM bekleme süresini değil literatür taraması ve deterministic tool çalışmaları dahil run'ın başından sonuna gerçek geçen süreyi ölçer.

Streamlit'teki **Geçmiş Kayıtlar** sekmesi her run için toplam token, ücret, süre; agent bazında model/token/ücret; ayrıca tek tek API çağrılarını gösterir.

Bir provider exact cost alanı döndürmezse `cost_complete=false` olur ve arayüzde ücret minimum bilinen toplam olarak işaretlenir.

## Genel multi-agent desenleri

| Desen | Ne yapar | Kullanım |
|---|---|---|
| `pipeline` | A → B → C zinciri | araştırma → analiz → eleştiri |
| `research_loop` | Teorisyen → Sceptik → revizyon | kısa açık uçlu araştırma |
| `debate` | ajanlar karşılıklı tartışır | pozisyon/argüman testi |
| `panel` | bağımsız cevaplar + sentez | fikir çeşitliliği |
| `TheoremResearchLab` | manager + compute + ledger + literature + audit | uzun süreli matematik/teorik CS araştırması |

## Trace ve denetlenebilirlik

Theorem Lab literature search ve deterministic tool sonuçlarını da trace'e kaydeder. Araştırma sonucu değerlendirirken önerilen standart:

```text
[IDEA] -> [OPEN] -> [COMPUTATION_PASS] -> [PROOF_CANDIDATE]
                                      -> formal checker / independent audit
                                      -> [PROVEN]

Her aşamada counterexample -> [FAIL]
```

Amaç çok sayıda "güzel ispat" üretmek değil, yanlış hipotezleri mümkün olduğunca erken ve ucuz biçimde öldürmektir.
