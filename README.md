# AI Lab

Çok ajanlı, doğrulama ve kalıcı araştırma hafızası destekli LLM araştırma laboratuvarı. OpenAI-uyumlu API sağlayıcılarıyla çalışır; varsayılan kullanım OpenRouter'dır.

Repo iki ayrı kullanım katmanı sunar:

1. **Genel multi-agent deneyleri:** pipeline, research loop, debate, panel.
2. **Theorem Research Lab:** açık matematik/teorik CS problemleri için kalıcı ledger, literatür screening, deterministic tools, adversarial verification, stop/resume, ayrı worker process ve evidence-gated proof ladder.

## Kurulum

```powershell
cd ailab
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env` içine API anahtarını ekle:

```text
OPENROUTER_API_KEY=...
```

Web arayüzü:

```powershell
.venv\Scripts\streamlit run app.py
```

### CodeExperiment için Docker/Podman

LLM tarafından üretilen Python artık **host üzerinde çalıştırılmaz**. `CodeExperimentAgent` ile gerçek Python execution istiyorsan Docker veya Podman kurulu olmalıdır. Container engine yoksa sistem `run_python` için fail-closed davranır; kodu host Python'a düşürmez.

Varsayılan image:

```text
python:3.12-slim
```

İstersen Code Experiment Agent ayar sayfasından image/engine/resource limitlerini değiştirebilirsin.

## Güncel mimari

```text
Streamlit UI
   |
   +-- Project Hub / ProjectPlanner
   |
   +-- theorem start/resume
          |
          v
   detached lab.worker process
          |
          v
   TheoremResearchLab (tek production engine)
      |
      +-- RunController     run.lock, stop, runtime cursor, retry policy
      +-- StepStore         SQLite step cache, partial resume, iteration snapshots
      +-- ResearchState     insan-okunabilir state.json + theorem graph + checkpoints
      +-- ToolRegistry      tek tool şeması + dispatch kaynağı
      +-- LiteratureClient  arXiv + Crossref screening
      +-- CodeExperiment    container-only generated Python
      +-- Z3 / Lean / TropicalGrid / reviewed ScriptTool
      |
      +-- Trace
           +-- trace.jsonl   core events
           +-- stream.jsonl  buffered reasoning/content stream
           +-- summary.json

runs/index.jsonl            run ↔ project index
```

Eski `theorem_lab.py`, `resumable_theorem_lab.py`, `partial_resume_theorem_lab.py`, `code_experiment_theorem_lab.py` ve `hardened_theorem_lab.py` artık ikinci bir workflow implementasyonu içermez; yalnız geriye-dönük import uyumluluğu için ince shim'lerdir. Üretim akışı `lab/theorem_engine.py` içindedir.

## Projeler ve ProjectPlanner

Araştırmalar project-centric yönetilir. **Projeler** sayfasında yeni proje oluşturabilir, arşivleyebilir, klonlayabilir, açabilir ve durdurulmuş projeye devam edebilirsin.

Yeni proje oluştururken yalnız doğal dil promptu vermen yeterlidir. `ProjectPlanner` şu alanlar için düzenlenebilir taslak üretir:

- proje adı ve immutable olmayan insan-okunur `project_id`,
- kısa açıklama,
- deney türü,
- ayrıntılı frozen problem,
- literatür sorgusu,
- etiketler.

Her proje ayrıca immutable bir `project_uuid` alır. Aynı `project_id` silinip daha sonra yeniden kullanılırsa eski run geçmişi yeni projeye otomatik bağlanmaz.

## Teorem araştırması ajanları

Varsayılan roller:

- `ResearchManager`
- `Theorist`
- `AdversarialCritic`
- `VerificationEngineer`
- `LiteratureScout`
- `IndependentAuditor`
- gerektiğinde `CodeExperimentAgent`

Her rol için ayrı model ve reasoning effort seçilebilir. Run sırasında kullanılan model/system prompt/temperature/reasoning effort `run_config.json` içine kaydedilir.

## Proof ladder: status LLM kararı değildir

Bir veya yüz LLM'in “doğru” demesi bir iddiayı `PROVEN` yapmaz. Manager yalnız **status isteği** yapar; gerçek status kod tarafındaki evidence guard tarafından belirlenir.

```text
OPEN
  |
  +-- successful deterministic computation ----------> COMPUTATION_PASS
  |
  +-- Verifier PASS + Critic != KILL ----------------> PROOF_CANDIDATE
  |
  +-- successful Lean checker
      + Verifier PASS
      + Critic != KILL ------------------------------> PROVEN

concrete counterexample --------------------------------> FAIL
research direction intentionally abandoned --------------> DROPPED
```

Kurallar:

- `COMPUTATION_PASS`: aynı aday için gerçekten başarılı deterministic computation gerekir. Manager metni yeterli değildir.
- `PROOF_CANDIDATE`: verifier `PASS` vermeli ve critic `KILL` etmemelidir.
- `PROVEN`: yalnız başarılı `LeanTool` sonucu `formal_verified=true` üretebilir; ayrıca verifier PASS ve critic not-KILL gerekir.
- `FAIL`: somut counterexample evidence ile kullanılır. Sadece “bu fikir kötü” LLM görüşü matematiksel FAIL yerine `DROPPED` olabilir.
- Guard bir status isteğini düşürürse trace'e `status_downgraded_by_guard` olayı yazılır.

## Formal doğrulama yolu

Formal adaylar şu akışla ilerler:

```text
Theorist / Verifier
   |
   +-- lean_draft
   v
formal/candidates/<name>.lean
   |
   +-- lean
   v
LeanTool / lake env lean
   |
   +-- returncode == 0
   v
formal_verified=true
```

Generated Lean host execution varsayılan olarak **kapalıdır**. Trusted/containerized Lean kurulumu yaptıysan açıkça:

```text
LAB_ALLOW_HOST_LEAN=1
```

verebilirsin. `LeanTool`, `lake` varsa `lake env lean` kullanır; böylece mathlib/lake projeleri çıplak `lean file.lean` yaklaşımına göre daha doğru çözülür. Generated Lean için obvious IO/process/metaprogramming özellikleri ayrıca reddedilir, fakat en güçlü kurulum formal checker'ı da container içinde çalıştırmaktır.

## CodeExperiment güvenlik modeli

İki Python mekanizmasını karıştırma:

### ScriptTool

`research_tools/` altındaki **insan tarafından review edilmiş** `.py` scriptlerini çalıştırır. LLM bu dizine keyfi script yazmaz.

### CodeExperimentAgent

LLM kendi proje workspace'ine deney scripti yazabilir/patch edebilir/okuyabilir. Fakat execution güvenlik sınırı AST filtresi değildir. Generated Python yalnız disposable container içinde çalışır:

```text
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges
--memory=...
--pids-limit=...
--cpus=...
--tmpfs /tmp:rw,noexec,nosuid
--mount <project workspace>:/workspace
```

Host home dizini, repo kökü, `.env` veya Docker socket container'a mount edilmez. Container'a API key aktarılmaz. Container engine yoksa execution reddedilir.

AST filtresi yalnız **defense in depth** katmanıdır. `open`, `eval`, `exec`, `__import__` vb. blocked isimler alias ataması dahil her load noktasında reddedilir; fakat Python AST filtresi tek başına sandbox olarak kabul edilmez.

`CodeExperimentAgent` `finish` diyebilmek için en az bir gerçek başarılı `run_python` kanıtına sahip olmalı ve son Python run'ı başarılı olmalıdır. Evidence manifest script/stdout/stderr hashleri ve immutable output dosyalarını içerir. Finite computation hiçbir zaman tek başına proof değildir.

## Stop / resume ve worker process

Uzun theorem run'ları Streamlit render thread'inde çalışmaz. UI yalnız worker process başlatır ve durum dosyalarını/trace'i izler. Bunun sonucu:

- tarayıcı sekmesini kapatabilirsin;
- Streamlit rerun araştırmayı öldürmez;
- aynı sayfadan/başka sekmeden `DURDUR` kullanılabilir;
- farklı projeler ayrı worker process'lerde paralel çalışabilir;
- aynı proje için `ProjectRunLock` ikinci eşzamanlı run'ı reddeder.

Kalıcı dosyalar:

```text
research_state/<project_id>/
  project.json
  problem_frozen.json
  state.json
  theorem_graph.json
  research_steps.sqlite3
  runtime.json
  run_config.json
  worker.json
  worker_request.json
  worker_result.md
  checkpoints/
  workspace/
```

`StepStore` SQLite içinde completed step cache, partial response ve frozen iteration snapshot tutar. Eski `step_cache.json` / `partial_steps.json` bulunursa bir kez migrate edilir.

### Resume bütünlüğü

Her iterasyon başında ledger context ve ledger revision **dondurulur**. Resume aynı iteration snapshot'ını kullanır. Proposal ayrıca content hash ile item'a bağlanır. Resume sonrası yeniden üretilmiş bir proposal mevcut ledger item'ıyla eşleşmiyorsa sistem evidence'ı yanlış claim'e yapıştırmak yerine `PAUSED_ERROR` ile durur.

Tamamlanmış LLM step fingerprint'inde model adı bilinçli olarak yoktur. Resume ekranında bozuk/deprecated model slug'ını değiştirirsen yalnız incomplete çalışma yeni modele geçer; bitmiş adımlar yeniden ücretlendirilmez. System prompt, temperature veya reasoning effort değiştirmek ise davranışı gerçekten değiştirdiği için ilgili fingerprint'i değiştirebilir.

Partial resume sırasında provider-visible `reasoning_details` model değişmemişse structured continuation olarak korunur. Model değiştiyse provider-specific structured state yeni modele gönderilmez; yalnız görünür reasoning/content soft context olarak kullanılır.

## Research ledger

`state.json` insan tarafından okunabilir bilimsel ledger'dır. High-frequency cache/partial data SQLite'a taşınmıştır.

Prompt context kronolojik “son 20 kayıt” değildir. Sistem:

- bütün `FAIL`/`DROPPED` conjecture'ları kompakt tombstone olarak **daima** taşır;
- bütün aktif conjecture'ları taşır;
- known/audit/counterexample kayıtlarında yalnız yakın geçmişi pencereye alır.

Böylece yüzlerce tur sonra eski bir ölü fikir sessizce prompt penceresinden kaybolmaz.

## Structured output

Theorem workflow için JSON parse hataları artık sessizce `OPEN/REVISE` default'una dönüşmez. Ortak `lab/json_io.py`:

1. code fence/object çıkarımı yapar;
2. raw LaTeX backslash, control character ve trailing comma gibi yaygın almost-JSON hatalarını deterministic olarak düzeltir;
3. yine parse olmazsa aynı ajanla **bir kez formatting-only repair çağrısı** yapar;
4. repair de başarısızsa araştırmayı fail-closed `PAUSED_ERROR` durumuna alır.

Belirsiz structured output bilimsel karar olarak kabul edilmez.

## Literature / novelty screening

`LiteratureClient` arXiv ve Crossref kullanır. arXiv sorgusu artık tüm cümleyi `all:"..."` exact phrase haline getirmez; problem/query içinden kompakt terimler çıkarıp `AND` ile alan sorgusu üretir.

Sıfır kayıt:

```text
NOVEL değildir
INCONCLUSIVE'dur
```

Trace'e `literature_search_inconclusive` yazılır ve LiteratureScout'a açıkça “boş sonuç novelty kanıtı değildir” denir. Screening yine kesin novelty ispatı değildir; yayın öncesi bağımsız literatür audit'i gerekir.

## Deterministic tools

`ToolRegistry` tool şeması ve dispatch için tek kaynaktır. Mevcut araçlar:

- `script`: review edilmiş `research_tools/*.py`;
- `z3`: SMT-LIB; solver timeout varsayılan 30 saniye;
- `tropical_grid`: küçük-n finite exact grid / counterexample search;
- `lean_draft`: `formal/candidates/*.lean` oluşturur;
- `lean`: formal candidate'ı kontrol eder;
- `code_experiment`: container içinde LLM-authored Python experiment loop.

Tool listesi prompt içine runtime string ekleyerek Agent nesnesini mutasyona uğratmaz; proposal schema `ToolRegistry` üzerinden üretilir.

## Trace, token, ücret ve performans

Her run collision-proof UUID'li klasör alır:

```text
runs/<timestamp_microseconds>_<uuid>_<experiment>/
  trace.jsonl
  stream.jsonl
  summary.json
```

`trace.jsonl` core olayları ve tamamlanmış LLM çağrılarını içerir. Token-level stream core trace'e yazılmaz; reasoning/content delta'ları `stream.jsonl` içinde yaklaşık 200 ms / 4 KB batch'ler halinde tutulur. Ham Loglar sayfası her saniye dosyayı baştan okumak yerine byte offset ile tail-read yapar.

`runs/index.jsonl` proje ↔ run eşlemesini indeksler; UI geçmiş listelemek için her run'ın trace dosyasını tekrar tekrar taramak zorunda değildir.

OpenRouter `usage.cost` sağlıyorsa gerçek çağrı maliyeti kaydedilir. Kayıtlar arasında:

```text
prompt_tokens
completion_tokens
reasoning_tokens
cached_tokens
total_tokens
cost_usd
latency_s
wall_time_s
```

bulunur.

## Retry politikası

OpenAI SDK'nın gizli retry katmanı kapalıdır:

```text
max_retries=0
```

ve explicit request timeout kullanılır. Retry/backoff yalnız research RunController/TheoremResearchLab katmanında yapılır ve her deneme trace'e girer. Böylece tek adımın görünmez SDK retry'ları nedeniyle beklenmedik şekilde katlanması engellenir.

## CI ve kalite

GitHub Actions bütün branch push'larında ve PR'larda çalışır:

- pytest: Python 3.10, 3.11, 3.12, 3.13;
- Ruff;
- mypy.

`pandas` artık doğrudan dependency'dir. Runtime-local `reasoning_settings.json`, `code_experiment_settings.json`, `runs/` ve `research_state/` git'e alınmaz.

## Güven sınırlarının kısa özeti

AI Lab şu iddiaları **yapar**:

- LLM opinion tek başına proof status veremez.
- Generated Python host üzerinde çalıştırılmaz.
- Generated Python container içinde network'süz/resource-limited çalışır.
- Tamamlanmış step'ler resume'da korunur.
- Claim/evidence resume uyuşmazlığı fail-closed olur.
- Bozuk JSON bilimsel karar olarak sessizce kabul edilmez.
- Boş literature search novelty sinyali değildir.

Şu iddiaları **yapmaz**:

- finite testing genel matematiksel proof'tur;
- literature screening kesin novelty proof'tur;
- LLM reasoning güvenilir formal proof'tur;
- AST filtre tek başına sandbox'tır;
- Lean checker'ın kendisi theorem statement'ın araştırma niyetiyle semantik olarak aynı olduğunu otomatik garanti eder. Bu bağ hâlâ audit edilebilir metadata + verifier/critic katmanıyla kontrol edilir.
