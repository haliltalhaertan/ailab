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
            | worker_request.json (`experiment_method`)
            v
     Detached Worker
        /       \
       v         v
Orchestrator   TheoremResearchLab
                  /    |     \
             StepStore RunController ToolRegistry
                |          |          |
              SQLite    lock/stop   Python/Z3/Lean
                           |
                     ResearchState
                           |
                      Trace / Audit
```

Beş deney türünün tamamı (`theorem_lab`, `research_loop`, `debate`, `pipeline`, `panel`) `experiment_method` ile aynı detached worker yürütme yoluna girer. Streamlit içinde doğrudan `Orchestrator` çalıştıran `execute_inline` yolu yoktur. Ana sayfa ve Research Control ortak canlı panelden aktif aşamayı, agent/model/effort bilgisini, reasoning/cevap akışını, token ve ücret metriklerini ve zaman çizelgesini gösterir; `DURDUR` isteği `stop.flag` üzerinden worker'a iletilir.

Theorem workflow'unun ana yürütme mantığı `lab/theorem_engine.py` içindedir; `TheoremResearchLab` sınıfı `lab/__init__.py` üzerinden de export edilir.

Başlıca runtime dosyaları:

```text
research_state/<project_id>/
  project.json              # proje metadata'sı; READY/ARCHIVED
  problem_frozen.json
  state.json                # araştırma ledger'ı
  theorem_graph.json
  runtime.json              # çalışma statüsünün tek kaynağı
  run_config.json
  tool_availability.json    # declared/runtime/effective tool capability snapshot
  research_steps.sqlite3
  worker.json               # gerçek worker pid/run_id/launched_at
  worker_launch.json        # launcher pid + platform/breakaway bilgisi
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

Araştırma ledger durumları:

```text
OPEN
REFUTATION_CANDIDATE
COMPUTATION_PASS
PROOF_CANDIDATE
PROVEN
FAIL
KNOWN
BARRIER
DROPPED
```

Ayrıca worker sağlığı için UI/runtime katmanında türetilen `STALE_RUNNING` tanısı vardır. Bu bir matematiksel evidence statüsü değildir: persisted `RUNNING` kaydı canlı `run.lock` sahibi veya güncel heartbeat ile doğrulanamadığında gösterilir. Kullanıcı `Stale run'ı temizle` işlemini açıkça seçerse stale kilit kaldırılır ve runtime `INTERRUPTED` olur; step cache ve partial çalışma korunur.

Merkezi evidence guard LLM'nin istediği status ile gerçekten mevcut evidence'ı karşılaştırır:

- `REFUTATION_CANDIDATE`: LLM'nin öne sürdüğü ama deterministic olarak doğrulanmamış karşıörnek; araştırma alanını kalıcı kapatmaz.
- `COMPUTATION_PASS`: anlamlı ve başarılı deterministic compute evidence gerekir. Z3 için en az bir assertion; tropical grid için en az bir gerçekten kontrol edilmiş case gerekir.
- `PROOF_CANDIDATE`: verifier/critic değerlendirmesi gerekir; formal ispat değildir.
- `PROVEN`: aynı ledger item/iteration/claim'e bağlı başarılı Lean evidence, temiz kaynak, axiom audit, verifier PASS ve critic'in KILL etmemesi gerekir.
- `FAIL`: yalnız deterministic olarak doğrulanmış counterexample yolu kalıcı matematiksel FAIL üretebilir. LLM-only negatif kanaat FAIL değildir.

Manager daha güçlü bir status ister fakat evidence yoksa durum otomatik olarak daha düşük güven seviyesine indirilir ve trace'e kaydedilir.

### Formal doğrulama yolu

LLM formal bir aday üretmek isterse `lean_draft` aracıyla proje içindeki `formal/candidates/` alanına Lean dosyası yazar. Engine aday kaynağını mevcut ledger item'ına, iteration'a ve claim hash'ine bağlar; dosyanın SHA-256 değeri de doğrulama zincirine alınır.

Lean source kapısı `sorry`, `admit`, `axiom`, `opaque`, `native_decide`, `set_option`, `partial def` ve diğer tanımlı escape-hatch kalıplarını reddeder. Checker `-DwarningAsError=true` ile çalıştırılır; compiler çıktısında `sorry`/axiom şüphesi görülürse başarı kabul edilmez. `#print axioms` sonucu izin verilen axiom kümesine karşı denetlenir.

Dolayısıyla yalnız `returncode == 0` olması `[PROVEN]` için yeterli değildir. Claim hash, item/iteration, theorem adı/türü, source SHA, source-clean ve axiom doğrulamaları aynı evidence zincirinde uyuşmalıdır. Lean kurulu değilse veya host Lean çalıştırmaya açıkça izin verilmemişse formal doğrulama başarısız/inconclusive kalır; LLM görüşü bunun yerine geçmez.

## Durdur / devam bütünlüğü

Tüm deney türleri Streamlit render thread'inde değil ayrı worker process'te çalışır. Tarayıcı kapansa bile worker yaşamaya devam edebilir. Ana sayfa ve Research Control'daki STOP isteği `stop.flag` üzerinden worker'a iletilir. Step-level kaldığı yerden devam semantiği theorem workflow'una aittir; theorem dışı workflow'lar aynı isteği yeni bir run olarak yeniden çalıştırır.

Proje `run.lock` kilidi mutable run işlemlerinden önce alınır. Worker kilidi almadan `run_config.json`, stale stop flag, worker identity veya runtime çalışma durumunu değiştirmez. Aynı projede ikinci worker lock alamazsa aktif worker'ın dosyalarını overwrite etmez; yalnız ayrı bir busy tanısı yazabilir.

Windows launcher önce `CREATE_BREAKAWAY_FROM_JOB` ile başlatmayı dener. Parent job bunu reddederse daha zayıf detached flag'lere geri döner ve `worker_launch.json` içinde `breakaway=false` kaydeder; breakaway'in her Windows ortamında garanti edildiği iddia edilmez.

`runtime.json` heartbeat ile güncellenir. RUNNING state için lock kayıp/ölü veya heartbeat 120 saniyeden eskiyse sistem bunu `STALE_RUNNING` olarak teşhis edebilir ve kullanıcıya güvenli resume yolu açar.

Tamamlanan step'ler SQLite `StepStore` içinde content fingerprint ile saklanır. Tamamlanan step cache payload'ları HMAC ile seal edilir; seal uyuşmazsa cache yeniden kullanılmaz. Yarım provider-visible çalışma `reasoning`, `reasoning_details` ve `content` ile birlikte partial kayıt olarak tutulur.

Her iteration başında ledger context, ledger revision ve next task dondurulur. Resume sırasında proposer'ın ürettiği claim dondurulmuş proposal ile uyuşmazsa yeni evidence eski conjecture'a sessizce bağlanmaz; run fail-closed biçimde `PAUSED_ERROR` durumuna geçer.

Bu gerçek provider KV-cache resume değildir. Provider'ın gizli inference state'i API tarafından verilmediğinde yalnız provider-visible reasoning/content güvenli biçimde yeniden kullanılabilir.

## Araştırma state'i bütünlük sınırı

`PROVEN` evidence kayıtları HMAC proof seal taşır ve bağlı Lean dosyasının canlı SHA-256 değeri yeniden kontrol edilir. Tamamlanmış StepStore cache kayıtları da HMAC ile doğrulanır. `LAB_EVIDENCE_HMAC_KEY` dışarıdan verildiğinde anahtar proje verisinin dışında tutulabilir. Bu değişken verilmezse StepStore HMAC anahtarı proje klasöründe tutulur; bu mod kazara/basit manuel düzenlemeleri saptamak için bir bütünlük kontrolüdür, proje dosyalarına erişebilen düşman bir modele karşı güvenlik imzası değildir.

Buna karşılık **bütün `state.json` dosyasının canonical items+events içeriği için global read-time seal uygulanmaz**. Audit'teki opsiyonel tam-state integrity maddesi bilinçli olarak kapsam dışında bırakılmıştır; proje dosya sistemini ve yerel HMAC anahtarını değiştirebilen bir yöneticiye karşı genel tamper-proof ledger garantisi verilmez. Güvenlik/evidence iddiaları yukarıdaki daha dar PROVEN ve cache kontrolleriyle sınırlıdır.

## CodeExperimentAgent güvenlik modeli

LLM tarafından üretilen Python **host Python process'inde çalıştırılmaz**. CodeExperimentAgent deney çalıştırabilmek için Docker veya Podman ister. Container runtime bulunamazsa sistem host execution'a düşmez; deney **fail closed** olur.

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

AST doğrulaması yalnız **best-effort defense-in-depth** preflight kontrolüdür ve güvenlik sandbox'ı değildir. Bilinen riskli import/attribute/dunder/string/call kalıplarını erkenden reddeder fakat Python AST politikasının tüm olası bypass'ları kapattığı varsayılmaz. Üretilen kod için tek yürütme güvenlik sınırı container izolasyonudur.

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

Theorem worker run başında executable capability'leri ölçer ve üç ayrı snapshot saklar: `declared_tool_availability` run'ın başlangıç evrenidir, `runtime_tool_availability` mevcut makinenin ölçümüdür, `effective_tool_availability` ise güvenli kesişimdir. Resume sırasında bir araç kaybolursa effective evren daralır; sonradan kurulan yeni bir araç aynı `STOPPED` / `PAUSED_ERROR` / `INTERRUPTED` run'ı sessizce genişletemez. Yeni capability kullanmak için yeni run başlatılır. Snapshot `tool_availability.json`, trace ve `run_config.json` içinde görünür; Research Control aynı effective durumu Lean/Z3/Script/Tropical/Container rozetleriyle gösterir.

Lean yalnız `LAB_ALLOW_HOST_LEAN=1` iken ve `lean` veya `lake` gerçekten PATH üzerinde bulunuyorsa LLM tool şemasına açılır. Z3 kurulumu, trusted script root'ları ve container runtime da ayrı ayrı ölçülür. Kullanılamayan bir araç prompttan çıkarılır ve dispatch tarafında fail-closed kalır. Tool yokluğu, timeout, syntax/format problemi veya infrastructure hatası matematiksel `FAIL` değildir; verifier açısından `INCONCLUSIVE` kalmalıdır.

- **ScriptTool:** yalnız review edilmiş `research_tools/` ve `problem_packs/` altındaki güvenilir script yollarını çalıştırır. Contract'sız legacy projede structured evidence trailer'ı olmayan, başarılı (exit-0) checked-in script `NUMERICAL_PASS` sayılabilir; frozen contract'a bağlı projeler trailer/binding eksikliğinde fail-closed kalır.
- **Z3Tool:** assertionsız SMT-LIB girdisini `inconclusive / no assertions` sayar; sat/unsat computation evidence için gerçek assertion gerekir.
- **TropicalGridTool:** nonnegative ağırlıkların seçilen sonlu gridinde min-plus shortest-path **fonksiyon eşitliğini** test eder. `GRID_PASS`, simple-path provenance polynomial ile formal/monomial-level eşitlik ispatı değildir. `gate_count` yalnız internal non-edge gate sayısıdır; `edge_gate_count` ayrıca raporlanır.
- **LeanTool:** claim-bound formal checker.
- **CodeExperimentAgent:** container içindeki yeni deney kodu döngüsü.

## Araştırma state'i ve eşzamanlılık

Aynı proje üzerinde aynı anda iki theorem worker çalıştırılamaz. `run.lock` atomik proje kilididir; ikinci process lock alamazsa aktif run'ın mutable state/config dosyalarına dokunmadan durur.

Çalışma statüsünün authoritative kaynağı `runtime.json`'dır. `project.json.status` yalnız proje metadata durumu olan `READY`/`ARCHIVED` için kullanılır; `worker.json` ise statü kaynağı değildir ve yalnız gerçek worker identity bilgisini taşır.

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

Yeni projeler `Projeler` sayfasından tek prompt ile ProjectPlanner kullanılarak oluşturulabilir. Deney başlatıldığında UI `experiment_method` içeren worker request'i yazar ve detached worker'ı başlatır. Beş deney türünün tamamı aynı canlı panelde izlenebilir ve `DURDUR` ile kesilebilir; `execute_inline` yürütme yolu yoktur. Theorem araştırmasında step-level resume Research Control'dan yapılır, diğer deneylerde yeniden çalıştırma yeni bir run açar.

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
Windows Python 3.14: pytest + Ruff + mypy
Ruff
mypy
container-integration
```

Normal pytest testlerinin container runtime gerektiren davranışsal testi Docker bulunmuyorsa `skip` eder. Ayrı `container-integration` GitHub Actions job'ı Docker bulunan Ubuntu runner üzerinde gerçek container davranışını kontrol eder: network izolasyonu, read-only root filesystem, yalnız writable workspace output alanı ve timeout sonrası container temizliği. AST unit testleri ayrıca best-effort policy davranışını doğrular fakat container güvenlik sınırının yerine geçmez.

Son audit-hardening değişiklikleri bu kalite kapısının tamamı yeşil olmadan `main`e alınmaz.

Amaç, çok sayıda ikna edici metin üretmek değil; yanlış hipotezleri mümkün olduğunca erken öldürmek ve her güçlü iddiayı gerçekten sahip olduğu evidence seviyesinde tutmaktır.
