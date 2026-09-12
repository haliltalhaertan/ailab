# ailab (LLM Lab) — denetim ve düzeltme çalışması, devir teslim

Bu bir devam görevidir. Aşağıdaki her sayı ve dosya:satır referansı gerçekten
çalıştırılmış komutlardan alındı; tahmin yok. Sen de aynı standarda uy: bir
bulguyu iddia etmeden önce prob ile üret, bir düzeltmeyi bitti demeden önce
testi koştur.

## Proje

- Konum: `C:\Users\MDP\Documents\ChatGPT\ailab` (git, `main` → `origin/main`)
- "LLM Lab": çok ajanlı LLM teorem araştırma laboratuvarı. Streamlit UI +
  proje başına detached worker. ~22k satır kod + ~8k satır test.
- Merkezi iddia: LLM üretimi Python host'ta ASLA çalışmaz (tek sınır container),
  ve `[PROVEN]` yalnız makine ile doğrulanmış Lean kanıtı + hash bağlama
  zinciriyle verilir.
- Venv: `.venv/Scripts/python.exe` (Python 3.14.3). Kabuk git-bash; native
  araçlara `C:/...` ileri-eğik yollar ver.

## Mevcut durum

HEAD: `9fbd035` "Close 2026-09-07 and 2026-09-10 audit findings"
(önceki: `3a31136`). **Commit yereldir, PUSH EDİLMEDİ.** Çalışma ağacı temiz.

Kapılar (commit sonrası temiz ağaçta ölçüldü):

```
.venv/Scripts/python.exe -m pytest tests docs/audits/2026-09-07/review_reproductions.py \
    -q -p no:cacheprovider -k "not container_integration"
→ 306 passed, 4 deselected

.venv/Scripts/python.exe -m ruff check lab tests app.py pages experiments  → temiz
.venv/Scripts/python.exe -m mypy lab                                        → 35 dosya, 0 hata
```

Denetim kayıtları: `docs/audits/2026-09-06/`, `2026-09-07/`, `2026-09-10/`.
Yeni regresyon paketi: `tests/test_audit_2026_09_10.py` (19 test).

## Bu commit'te kapatılanlar

Kullanıcının kendi bekleyen çalışması (R01–R03) + bu turda kapatılan F01–F03:

- **R01** Lean binder bağlama: `theorem_type` tüm parametre/varsayımları
  `∀ <binders>, <conclusion>` olarak içermek zorunda.
- **R02** `.run.guard` üzerinde OS kilidi (Windows byte-range, POSIX flock);
  `run.lock` yalnız sahiplik metadata'sı.
- **R03** Formal doğrulama sürüm kapısı (`FORMAL_VERIFICATION_VERSION = 2` +
  `theorem_statement_verified`); eski formal cache yeni kapıyı atlayamaz.
- **F01** Sahipsiz `run.lock` (boş/yarım JSON/çözümlenemeyen) projeyi kalıcı
  kilitliyordu → artık sahipsiz sayılıp geri alınabiliyor
  (`lab/integrity.py:_stale`). Canlı sahip, yabancı host, ölü-pid davranışları
  değişmedi; 12 process yarış testi tek kazanan verdi.
- **F02** `read_json_tolerant` geçersiz UTF-8'de `UnicodeDecodeError` ile
  çöküyordu (26 çağrı yeri) → `UnicodeDecodeError` + `ValueError` yakalanıyor.
- **F03** `sorryAx` formal kapıdan geçiyordu (`\baxiom\b` kelime sınırı) ve
  `LAB_LEAN_ALLOWED_AXIOMS` ile izin listesine eklenebiliyordu →
  `LeanTool.DENIED_AXIOMS` (`sorryAx`, `Lean.ofReduceBool`,
  `Lean.trustCompiler`) ayrıca aranıyor, ortamdan güvenilir yapılamıyor.

## AÇIK MADDELER — öncelik sırasıyla

### 1. PROVEN semantik boşluğu (en önemli, tasarım kararı gerekir)

`lab/theorem_engine.py:1528-1529` Lean başarısında `requested_status`'u manager
ne isterse istesin otomatik `PROVEN`'a yükseltiyor. Daha derin sorun:
`claim_hash = content_fingerprint("claim:v1", item.claim)` iddianın *kimliğini*
bağlıyor, *anlamını* değil. `theorem_type` yalnız Lean kaynağının kendi tipiyle
karşılaştırılıyor; formal ifadenin ledger'daki claim'i gerçekten formüle
ettiğini denetleyen kod YOK. Zor bir konjektürün kaydına kendi içinde tutarlı
ama alakasız bir teorem (`theorem trivial_ok : True := trivial`) bağlanabilir;
kalan iki koşul (verifier `PASS`, critic `!= KILL`) iki LLM çıktısıdır.

Bu turda koda dokunulmadı, README'ye açık uyarı eklendi. Kalıcı çözüm için
formal ifade ↔ claim tutarlılığını denetleyen bağımsız bir adım gerekir.
Otomatik yükseltmenin kaldırılıp manager'ın açık talebinin şart koşulması
tartışılmalı. **Bu madde devredilemez, kullanıcıyla karar verilmeli.**

### 2. Mühür kapsamı (mekanik, devredilebilir)

- `lab/step_store.py:283-301` `put_iteration_snapshot` HİÇ mühürlenmiyor.
  Prob ile doğrulandı: `ledger_context` → `"FORGED LEDGER CONTEXT"` yazıldı ve
  `get_iteration_snapshot` sorgusuz geri verdi. Bu kritik, çünkü README'nin
  "dondurulmuş proposal ile uyuşmazsa fail-closed `PAUSED_ERROR`" iddiası
  mühürlü bir tarafı mühürsüz bir referansla karşılaştırıyor.
- `lab/step_store.py:206-218` `partials` tablosu mühürsüz; `get_partial`
  doğrulama yapmıyor. Partial içerik `_resume_messages` ile prompt'a giriyor.
- `steps` tablosunun `status`/`fingerprint` SÜTUNLARI mühür dışında;
  `counts()`/`list_steps` bunları mühürsüz okuyor.
- `lab/step_store.py:146-167` `_seal_existing_steps_once` eski satırların
  hepsini geçerlilik denetlemeden taze anahtarla yeniden mühürlüyor →
  migrasyon öncesi yerleştirilmiş sahte cache "doğrulanmış" oluyor.

### 3. State dayanıklılığı (mekanik)

- `lab/run_controller.py:118-137` bozuk `runtime.json` → `default_runtime()` →
  `set_runtime` bunu tam kayıt sanıp `atomic_json` ile DİSKE YAZIYOR. Prob:
  `completed_iterations` 7 → 0, `next_task` boşaldı. Okuma fail-closed olmalı.
- `lab/runtime_health.py:148-151` `cleanup_stale_run` canlı bir `run.lock`'ı
  sahiplik doğrulaması yapmadan siliyor; heartbeat gecikmesinde iki writer.
- `lab/ui_project_settings.py:189,199` `force_stop_worker` kill'den ÖNCE
  okunmuş bayat `runtime` dict'ini koşulsuz `unlink` sonrası geri yazıyor.
- `lab/runtime_health.py:92-97` heartbeat duvar saati (ISO timestamp) üzerinden;
  sistem uykusu/saat kayması canlı worker'ı `STALE_RUNNING` gösteriyor.
- `lab/step_store.py` her metot: `with self._connect() as con:` transaction'ı
  commit ediyor ama BAĞLANTIYI KAPATMIYOR. Streamlit her rerun'da yeni
  `StepStore` kuruyor (`pages/3_Research_Control.py:89`) → Windows'ta handle
  sızıntısı, `ProjectManager.delete`'teki `shutil.rmtree` PermissionError,
  `-wal` checkpoint alamıyor. `contextlib.closing` ile sar.

### 4. Maliyet ve güvenlik nüansları

- `lab/client.py:160-162` `explicit` yoksa `max_tokens` = katalog kapasitesi.
  `LAB_EMERGENCY_MAX_TOKENS` `.env.example`'da yorum satırı (varsayılan kapalı).
  Run seviyesinde kümülatif USD tavanı + UI'da canlı harcama göstergesi yok;
  telemetri zaten toplanıyor, sadece kapı yok.
- `lab/integrity.py:339` HMAC anahtarı korumaya çalıştığı verinin yanında
  (`.evidence_hmac.key`) ve sistem `LOCAL_PROJECT_KEY` moduna SESSİZCE düşüyor.
  Prob: aynı anahtarla yeniden imzalanan sahte payload `get_step`'ten geçti.
  Mod `runtime.json`/trace/UI rozetinde görünür olmalı; `PROVEN`,
  `EXTERNAL_ENV` yokken yazılmamalı.
- `lab/code_experiment.py:142` `python:3.12-slim` digest ile sabitlenmemiş.
- `lab/code_experiment.py:69,213-216` AST politikası çağrıyı İSİMLE yakalıyor;
  `from operator import attrgetter as ag` sonrası `ag(...)` geçiyor. Gerçek risk
  DÜŞÜK (tek güvenlik sınırı container ve o sağlam), ama docstring'in iddiası
  yanlış. Ya import alias'ını izle ya da kontrolü kaldırıp yanlış güven verme.
- `lab/json_io.py:20-26` `IncompleteJSONObject` kesik çıktıda kayıtlı değer ne
  olursa olsun `status→OPEN`, `decision→REVISE`, `verdict→INCONCLUSIVE`
  döndürüyor; critic'in gerçek `KILL`'i maskeleniyor. Yön muhafazakâr (PROVEN
  veya FAIL üretemez), ama README'nin "sessiz default kullanılmaz" ifadesi
  teknik olarak yanlış. Kod kendi docstring'inde kabul ediyor, README etmiyor.
- README'de `--tmpfs /tmp:rw,noexec,nosuid,size=64m` yazmıyor ama komutta var;
  "yalnız workspace writable" ifadesi bu yüzden tam doğru değil (etki düşük).

### 5. Mimari borç

`lab/theorem_engine.py:1314 _run_inner()` — **386 satır, cyclomatic ~86**.
Repoda 60 satırı aşan 40, 100'ü aşan 10 fonksiyon var. Ayırma yerleri:
1. Tool dispatch + evidence binding → `IterationToolRunner`
2. Iteration snapshot / resume kontrolü → `IterationFreezer`
3. LLM çağrı + cache + partial → `CachedAgentCaller` (`_call` tek başına 148 satır)
4. Checkpoint/freeze → `CheckpointWriter`
5. Telemetri meta → `TelemetryRecord` dataclass

Ayrıca `lab/research_contract.py:313 evaluate_target_transition()` (147 satır,
CC 56) ve `lab/status_guard.py:20 choose_status()` (156 satır, CC 43) — bunlar
sistemin KARAR verdiği yerler, en okunur olması gereken kod.

## TUZAKLAR — bunları bilmeden zaman kaybedersin

1. **`docs/audits/2026-09-06/audit_reproductions.py::test_a08_...` FAIL EDİYOR
   ve bu bir regresyon DEĞİL.** Eskimiş harness: sayfadan yalnız iki fonksiyonu
   AST ile çıkarıyor, `consume_log_chunk` importunu içermiyor →
   `NameError`. 2026-09-07 raporunda da not edilmiş. Değişiklikler geri alınmış
   bir kopyada da aynı şekilde fail ettiği doğrulandı. Bunu düzeltmeye çalışma,
   ya harness'ı güncelle ya da kapsam dışı bırak.
2. **CRLF gürültüsü:** `git diff --stat` şişkin görünür (`lab/integrity.py`
   164 satır göründü, 28'i whitespace'ti). Gerçek değişikliği görmek için
   `git diff -w` kullan. Commit ederken `git -c core.safecrlf=false commit`.
3. **Git kimliği bu repoda tanımlı DEĞİL.** Global ayara dokunma; commit'i
   `git -c user.name="Halil Talha Ertan" -c user.email="haliltalhaertan@mail.com"`
   ile at (geçmişteki kimlik bu).
4. **`docs/audits/**/__pycache__`** commit öncesi temizle:
   `find docs -name "__pycache__" -type d -exec rm -rf {} +`
5. **`pytest --timeout` YOK** (eklenti kurulu değil); kullanma.
6. Audit script'leri `test_*.py` deseni dışında olduğu için CI'ın `pytest -q`
   koşusuna takılmaz; kasten öyle. `docs/audits/.../review_reproductions.py`'yi
   koşturmak için yolunu açıkça vermelisin.

## Muse Code (WSL) kullanacaksan

Muse salt-okunur denetçi ve tarif edilmiş mekanik düzeltmeler için işe yarıyor:
bu turda F01'i TDD ile doğru düzeltti (4 satır, 5 test, kısıtlara uydu).
Doğrulanmış çağrım:

```
wsl.exe -d Ubuntu -e bash -lc 'export PATH="$HOME/.local/bin:$PATH"; cd <dizin>;
  setsid nohup muse exec --trust-workspace --disable-write --disable-approval \
  --prompt-file ~/muse-work/prompt.txt > ~/muse-work/out.log 2>&1 < /dev/null &'
```

- Salt-okunur denetim için `--disable-write`; yazması gerekiyorsa kaldır.
- `--disable-approval` şart, yoksa headless exec approval'da sonsuz asılır.
- Prompt'u WSL home'a kopyala (`~/muse-work/`), `/mnt/c` log yolu `>` ile
  çalışmadı; `/tmp` de VM restart'ta silinir.
- Çıktıyı `tr -d '\0'` ile süz.
- WSL'de pytest kurulumu: `python3 -m pip install --user --break-system-packages
  -e ".[dev]"` (PEP 668 engeli var, `python3-venv` yok).
- **WSL'de 2 test ORTAM kaynaklı fail eder:**
  `tests/test_audit_2026_09_06.py::test_a02_*` — `subprocess.Popen(["python",...])`
  çağırıyor, Ubuntu'da yalnız `python3` var. Muse'a bunu BAŞTAN söyle, yoksa
  kendi hatası sanıp kovalar.
- **Kritik:** Muse'un raporu kanıt değil. Bu turda gördüğü koddan FARKLI bir
  sürümü düzeltti (klonda `.run.guard` yoktu). Yamasını gerçek koda uygula ve
  KENDİ probunla yeniden ölç.
- Muse exploit kodu yazma noktasında safety-refusal ile düşebilir (bu turda
  güvenlik ajanı öyle düştü); o kısmı kendin tamamla.

## ÇALIŞMA YÖNTEMİ — buna uy

1. **Prob olmadan bulgu yok.** Bu turda iki bağımsız denetim (Hermes + Muse)
   aynı beş kritik noktaya indi ama farklı şeyler kaçırdı; ikisi de kodu okuyup
   emin olduğunu sandığı yerde yanıldı, ikisi de prob koşturunca düzeldi.
   Bir anlaşmazlık çıktığında ölçümle karara bağla, tartışmayla değil.
2. **TDD:** önce kırmızı test, sonra minimal düzeltme, sonra tam paket.
   İlgisiz refactor yapma.
3. **Regresyon kısıtlarını açıkça yaz.** F01'de "canlı sahip asla reclaim
   edilmemeli / yabancı host korunmalı / ölü-pid davranışı korunmalı" kısıtları
   olmasa düzeltme sessizce mutual exclusion'ı bozabilirdi.
4. **Bir failure gördüğünde önce "bu benim mi" diye ölç.** Değişiklikleri geri
   alınmış bir kopyada aynı testi koştur.
5. Kullanıcı Türkçe yazıyor, Türkçe cevap ver; kod/tanımlayıcılar İngilizce.
6. Repo'nun kendi diline uy: yorumlar ve hata mesajları Türkçe, docstring'ler
   İngilizce, satır uzunluğu 120, ruff `E4,E7,E9,F`.

## İLK ADIM ÖNERİSİ

Kullanıcıya iki şeyi sor, sonra başla:
- `9fbd035` push edilsin mi? (yerel duruyor)
- Sıradaki madde hangisi olsun? Öneri: **Madde 2 (mühür kapsamı)** — mekanik,
  test edilebilir, ve sistemin en yüksek sesli iddiasını (fail-closed resume)
  gerçekten fail-closed yapıyor. Madde 1 (PROVEN semantiği) daha önemli ama
  tasarım kararı gerektiriyor, tek başına ilerletilemez.
