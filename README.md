# LLM Lab

Çok ajanlı, doğrulama ve kalıcı araştırma hafızası destekli LLM araştırma laboratuvarı. OpenAI uyumlu herhangi bir API sağlayıcısıyla çalışır (varsayılan: OpenRouter).

Bu repo iki kullanım katmanı sunar:

1. **Genel multi-agent deneyleri:** pipeline, research loop, debate, panel.
2. **Theorem Research Lab:** açık matematik/teorik CS problemleri için manager + teorisyen + adversarial critic + verifier + literature scout + bağımsız auditor; kalıcı conjecture ledger, deterministic compute araçları, literature screening, checkpoint/freeze ve durdur/devam desteği.

## Kurulum

```powershell
cd ailab
python -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item .env.example .env
```

Test araçları için:

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
```

`.env` içine en az bir API anahtarı koy:

```text
OPENROUTER_API_KEY=...
```

`LAB_*_MODEL` değişkenleri yalnız varsayılan model seçimleridir. Streamlit arayüzünde her agent için OpenRouter modelini ayrı seçebilirsin.

## Mimari

```text
Streamlit UI / Research Control
            |
            v
     Detached Worker
            |
            v
   TheoremResearchLab
      /    |     \
StepStore RunController ToolRegistry
   |          |          |
 SQLite    lock/stop   Python/Z3/Lean
            |
      ResearchState
            |
       Trace / Audit
```

Üretim theorem workflow'u yalnız `lab/theorem_engine.py` içinde bulunur. Eski theorem-lab modülleri yalnız compatibility shim'dir; araştırma mantığının ikinci kopyasını taşımaz.

Başlıca runtime dosyaları:

```text
research_state/<project_id>/
  project.json
  problem_frozen.json
  state.json
  theorem_graph.json
  runtime.json
  run_config.json
  research_steps.sqlite3
  run.lock                  # yalnız aktif run sırasında
  checkpoints/
  workspace/

runs/
  index.jsonl
  <run_id>/
    trace.jsonl
    stream.jsonl
    summary.json
```

## Theorem Research Lab

Amaç LLM'leri "ispat üreten chatbot" olarak değil, kontrollü ve denetlenebilir bir araştırma sistemi içinde kullanmaktır.

```text
                    Research Manager
                          |
          +---------------+---------------+
          |               |               |
       Theorist       Adversarial       Verifier
                          Critic            |
          |               |        deterministic tools
          +---------------+---------------+
                          |
                    Evidence Guard
                          |
                 Research State / Graph
                          |
                  Literature Scout
                          |
                 Independent Auditor
```

### Kanıt merdiveni

Bir LLM'nin veya birden fazla LLM'nin "doğru görünüyor" demesi bir iddiayı `[PROVEN]` yapmaz.

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

Merkezi evidence guard LLM'nin istediği status ile gerçekten mevcut evidence'ı karşılaştırır:

- `COMPUTATION_PASS`: deterministic başarılı compute evidence gerekir.
- `PROOF_CANDIDATE`: verifier/critic değerlendirmesi gerekir; formal ispat değildir.
- `PROVEN`: başarılı formal checker sonucu ve `formal_verified=true` metadata gerekir.
- `FAIL`: deterministic counterexample veya açık counterexample evidence gerekir.

Manager daha güçlü bir status ister fakat evidence yoksa durum otomatik olarak daha düşük güven seviyesine indirilir ve trace'e kaydedilir.

### Formal doğrulama yolu

LLM formal bir aday üretmek isterse `lean_draft` aracıyla proje içindeki `formal/candidates/` alanına Lean dosyası yazar. Daha sonra `LeanTool` dosyayı gerçek Lean checker ile çalıştırır. Yalnız checker başarıyla tamamlanırsa `formal_verified=true` üretilebilir ve `PROVEN` gate'i açılabilir.

Lean kurulu değilse formal doğrulama başarısız/inconclusive kalır; LLM görüşü bunun yerine geçmez.

## Durdur / devam bütünlüğü

Theorem run'ı Streamlit render thread'inde değil ayrı worker process'te çalışır. Tarayıcı kapansa bile worker yaşamaya devam edebilir. Research Control sayfasındaki STOP isteği `stop.flag` üzerinden worker'a iletilir.

Tamamlanan step'ler SQLite `StepStore` içinde content fingerprint ile saklanır. Yarım provider-visible çalışma `reasoning`, `reasoning_details` ve `content` ile birlikte partial kayıt olarak tutulur.

Her iteration başında ledger context, ledger revision ve next task dondurulur. Resume sırasında proposer'ın ürettiği claim dondurulmuş proposal ile uyuşmazsa yeni evidence eski conjecture'a sessizce bağlanmaz; run fail-closed biçimde `PAUSED_ERROR` durumuna geçer.

Bu gerçek provider KV-cache resume değildir. Provider'ın gizli inference state'i API tarafından verilmediğinde yalnız provider-visible reasoning/content güvenli biçimde yeniden kullanılabilir.

## CodeExperimentAgent güvenlik modeli

LLM tarafından üretilen Python **host Python process'inde çalıştırılmaz**. CodeExperimentAgent deney çalıştırabilmek için Docker veya Podman ister.

Container şu güvenlik sınırlarıyla başlatılır:

```text
network = none
root filesystem = read-only
capabilities = drop ALL
no-new-privileges
RAM limit
PID limit
CPU limit
timeout
stdout/stderr byte limit
yalnız project workspace writable bind mount
```

Container runtime bulunamazsa sistem host execution'a düşmez; deney **fail closed** olur.

AST doğrulaması ayrıca defense-in-depth sağlar: `open`, `eval`, `exec`, `__import__`, `os`, `subprocess`, `socket` gibi doğrudan veya alias edilmiş riskli yollar reddedilir. Asıl security boundary AST değil container'dır.

Agent'ın workspace araçları:

```text
write_file
read_file
list_files
patch_file
run_python
finish
```

`finish` ancak en az bir gerçek başarılı `run_python` evidence'ı varsa kabul edilir ve son run başarısızsa başarılı deney diye kapanamaz. Evidence manifest script/stdout/stderr SHA-256 hashlerini, return code'u, süreyi ve çalışma kimliğini taşır.

Hesaplamalı test ne kadar geniş olursa olsun otomatik `[PROVEN]` değildir; evidence seviyesi `COMPUTATION_ONLY` olarak tutulur.

## Literature / novelty screening

`LiteratureClient` arXiv ve Crossref üzerinde aday yayınları tarar. Uzun bir sorgu tek bir exact quoted phrase'e dönüştürülmez; birden fazla daha sağlam sorgu varyantı denenir.

Arama sonucu sıfırsa sistem bunu:

```text
INCONCLUSIVE
```

olarak yorumlar. "Sonuç bulamadım" hiçbir zaman "bu sonuç yenidir" kanıtı değildir. Kritik novelty iddiasında bağımsız literatür audit'i gerekir.

## Structured output

Theorem pipeline'daki yapılandırılmış LLM çıktıları fail-closed parse edilir:

1. JSON doğrudan parse edilir.
2. Yaygın bozuk JSON/LaTeX escape hataları deterministic repair ile denenir.
3. Gerekirse yalnız formatı düzeltmek için tek bir LLM repair çağrısı yapılır.
4. Hâlâ parse edilemiyorsa sessiz default kullanılmaz; run `PAUSED_ERROR` olur.

## Deterministic tools

`ToolRegistry` theorem promptuna sunulan tool şemalarıyla gerçek dispatch'in tek kaynağıdır.

- **ScriptTool:** yalnız review edilmiş `research_tools/` scriptlerini çalıştırır.
- **Z3Tool:** SMT-LIB doğrulaması; solver timeout'u vardır.
- **TropicalGridTool:** küçük-n exhaustive counterexample araması; PASS genel ispat değildir.
- **LeanTool:** formal checker.
- **CodeExperimentAgent:** container içindeki yeni deney kodu döngüsü.

## Araştırma state'i ve eşzamanlılık

Aynı proje üzerinde aynı anda iki theorem worker çalıştırılamaz. `run.lock` atomik proje kilididir; ikinci process lock alamazsa state/cache'e yazmadan durur.

Run klasörleri mikro-saniye + UUID tabanlı benzersiz `run_id` kullanır. İnsan tarafından okunabilir `project_id` yanında immutable `project_uuid` vardır; bir proje silinip aynı ID ile yeniden oluşturulursa eski run geçmişi yeni projeye bağlanmaz.

## Trace / log ölçeklenmesi

LLM çağrı metadata'sı, tool sonuçları, state değişimleri ve checkpoint'ler `trace.jsonl` içine yazılır. Yüksek hacimli streaming delta'ları ayrı `stream.jsonl` dosyasında buffer edilir.

Ham Loglar sayfası dosyaları her yenilemede baştan okumak yerine byte offset ile tail eder. `runs/index.jsonl` proje/run geçmişini indeksler.

Her LLM çağrısında mümkün olduğunda:

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
requested reasoning effort
```

kaydedilir. `summary.json` run toplamlarını tutar.

## Web arayüzü

```powershell
.venv\Scripts\streamlit run app.py
```

Yeni projeler `Projeler` sayfasından tek prompt ile ProjectPlanner kullanılarak oluşturulabilir. Theorem Research başlatıldığında UI worker request'i yazar ve detached worker'ı başlatır. Araştırma ilerlemesi Research Control ve Ham Loglar sayfalarından izlenir.

CodeExperimentAgent kullanacaksan Windows'ta Docker Desktop veya uyumlu bir Podman kurulumu gerekir. Container runtime yoksa metinsel theorem araştırması çalışabilir; generated-code experiment adımı güvenlik gereği başarısız olur.

## CLI

Örnek:

```powershell
.venv\Scripts\python experiments\theorem_research.py "Let P_n be the simple s-t path provenance polynomial of K_n over the min-plus tropical semiring. Improve either the O(n^3) upper bound or the Omega(n^2) lower bound." tropical-circuit 6
```

Aynı `project_id` ile tekrar çalıştırılırsa frozen problem ve kalıcı state korunur.

## CI / kalite kapısı

GitHub Actions şu kontrolleri çalıştırır:

```text
pytest: Python 3.10
pytest: Python 3.11
pytest: Python 3.12
pytest: Python 3.13
Ruff
mypy
```

Container execution testleri Docker Hub/network'e bağımlı değildir; external runtime deterministik test double ile doğrulanırken production command'in network/rootfs/capability/resource izolasyon flagleri ayrıca assert edilir.

Amaç, çok sayıda ikna edici metin üretmek değil; yanlış hipotezleri mümkün olduğunca erken öldürmek ve her güçlü iddiayı gerçekten sahip olduğu evidence seviyesinde tutmaktır.
