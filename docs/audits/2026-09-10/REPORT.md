# Bağımsız denetim — 10 Eylül 2026

Temel sürüm: `3a31136` (PR #37) + commit edilmemiş 2026-09-07 düzeltmeleri (R01–R03).

Yöntem: iki bağımsız denetçi aynı kod tabanını birbirinden habersiz inceledi
(Hermes çok-ajanlı denetim + Muse Code salt-okunur denetim). Her bulgu, iddia
edilmeden önce çalıştırılabilir bir prob ile üretildi; iki denetçinin
anlaşamadığı bir bulgu ayrı bir itiraz turunda ölçümle karara bağlandı.

## Kapatılan bulgular

### F01 — Sahipsiz `run.lock` projeyi kalıcı kilitliyordu (high)

`ProjectRunLock.acquire()` `run.lock` dosyasını `O_EXCL` ile yaratıp içine JSON
metadata yazar. Dosya yaratıldıktan sonra JSON yazılmadan process ölürse
(hard kill, güç kesintisi, disk dolu) geriye 0 baytlık veya yarım bir dosya
kalıyordu. `_read_lock` bunu `{}` okuyor, `_stale({})` ise `host`/`pid` boş
olduğu için `False` dönüyordu — dolayısıyla reclaim hiç denenmiyor ve her
`acquire()` `ProjectBusyError` veriyordu.

Kurtarma yolları da kapalıydı. `cleanup_stale_run` yalnızca `runtime.json`
içindeki status `RUNNING` iken çalışır (`stale_running_reason` status farklıysa
erken döner), ancak `worker.py` kilidi runtime'a `RUNNING` yazmadan önce alır;
yani crash anında runtime önceki değerinde (`INTERRUPTED` / `NEW` / yok) kalır.

Ölçüm (5 senaryo): `RUNNING` durumunda cleanup kurtarıyordu, diğer dördünde
UI'da "Stale run'ı temizle" düğmesi hiç görünmüyor ve cleanup reddediyordu.
`force_stop_worker` kilidi koşulsuz siliyor ancak bağlı olduğu UI düğmesi
`disabled=not running` ve boş kilitte `project_lock_is_live` `False` döndüğü
için tıklanamıyordu. "Yeniden çalıştır" ise `acquire`'da ölen worker doğuruyordu.

Düzeltme: sahiplik iddiası taşımayan (boş/çözümlenemeyen) lock sahipsiz sayılır
ve geri alınabilir. Gerçek eşzamanlılık sınırı `.run.guard` üzerindeki OS kilidi
olduğu için bu yarışa yol açmaz. Canlı sahip, yabancı host ve ölü-pid reclaim
davranışları değişmedi.

### F02 — `read_json_tolerant` geçersiz UTF-8'de patlıyordu (medium)

Fonksiyon `json.JSONDecodeError`, `OSError` ve `PermissionError` yakalıyor ancak
`UnicodeDecodeError` yakalamıyordu. Yarım yazılmış bir JSON dosyası çok baytlı
bir karakterin ortasında kesilirse (Türkçe metinlerde olağan) okuma çöküyordu.
Fonksiyon 26 çağrı yerinde kullanılıyor: `runtime.json`, `project.json`,
`worker.json`, `research_contract`, `problem_frozen`.

Düzeltme: `UnicodeDecodeError` ve `ValueError` de yakalanır; adının vaat ettiği
gibi bozuk girdide default döner.

### F03 — `sorryAx` formal kapıdan geçiyordu (high)

İki ayrı delik birleşiyordu:

1. `_compiler_mentions_unsafe_proof` `re.search(r"\baxiom\b")` kullanıyordu.
   Kelime sınırı nedeniyle `sorryAx` yakalanmıyordu (ölçüldü:
   `'uses axiom Foo.Bar'` → `True`, `'sorryAx'` → `False`).
2. İzin verilen axiom kümesi `LAB_LEAN_ALLOWED_AXIOMS` ile sınırsız
   genişletilebiliyordu.

Birleşince: `LAB_LEAN_ALLOWED_AXIOMS="...,sorryAx"` ile `sorry` bağımlı bir
ispat `[PROVEN]` alabiliyordu.

Düzeltme: `DENIED_AXIOMS` (`sorryAx`, `Lean.ofReduceBool`, `Lean.trustCompiler`)
tanımlandı. Bu adlar derleyici çıktısında ayrıca aranır, `#print axioms`
sonucunda görülürse doğrulama düşer ve ortam değişkeniyle izin listesine
eklenemezler.

## Kapatılmayan — bilinçli olarak belgelendi

### Semantik bağlama boşluğu

`claim_hash` doğal dildeki iddianın kimliğini bağlar, anlamını değil.
`theorem_type` yalnız Lean kaynağının kendi tipiyle karşılaştırılır; formal
ifadenin ledger'daki claim'i gerçekten formüle ettiğini denetleyen otomatik bir
kontrol yoktur. Zor bir konjektürün kaydına, kendi içinde tutarlı ama alakasız
bir teorem bağlanabilir; kalan iki koşul (verifier `PASS`, critic `!= KILL`) iki
LLM çıktısıdır. Ayrıca `theorem_engine.py` Lean başarısında `requested_status`'u
otomatik `PROVEN`'a yükseltir.

Bu bir tasarım kararı gerektirdiği için bu turda kod değişikliği yapılmadı;
README'ye açık uyarı eklendi. Kalıcı çözüm için formal ifade ile claim
tutarlılığını denetleyen bağımsız bir adım gerekir.

### Mühür kapsamı

HMAC mührü yalnız tamamlanmış step cache'i kapsıyor. `iteration_snapshots` ve
`partials` tabloları mühürsüz olduğu hâlde resume akışında güvenilerek
kullanılıyor (prob ile doğrulandı: `ledger_context` ve `next_task` sahte
değerlerle değiştirilip geri okunabildi). Ayrıca `_seal_existing_steps_once`
eski satırları geçerlilik denetlemeden yeniden mühürlüyor. Bir sonraki tura
bırakıldı.

### Diğer açık maddeler

- Bozuk `runtime.json` okunduğunda default'a düşülüp bu değer diske geri
  yazılıyor; ilerleme sessizce sıfırlanabiliyor (prob ile doğrulandı:
  `completed_iterations` 7 → 0).
- `StepStore` sqlite bağlantıları kapatılmıyor; Windows'ta handle sızıntısı ve
  `shutil.rmtree` hatasına yol açabilir.
- Varsayılan `max_tokens` modelin tam kapasitesine eşitleniyor; run seviyesinde
  maliyet tavanı yok.
- `cleanup_stale_run` canlı bir kilidi silmeden önce sahiplik doğrulaması
  yapmıyor; heartbeat gecikmesinde iki writer oluşabilir.

## Doğrulama

- Yeni regresyon testleri: `tests/test_audit_2026_09_10.py`, 19 test.
  Kırmızı faz 14 fail / 5 pass, düzeltme sonrası 19 pass.
- Tam paket + 2026-09-07 repro testleri: **313 geçti**, 4 Docker testi atlandı.
- Ruff temiz; mypy 35 kaynak dosyasında temiz.
- `docs/audits/2026-09-06/audit_reproductions.py::test_a08_...` fail veriyor.
  Bu, 2026-09-07 raporunda da not edilen eskimiş harness'tır: sayfadan yalnız
  iki fonksiyonu AST ile çıkarıp `consume_log_chunk` importunu içermiyor.
  Değişikliklerim geri alınmış ağaçta da aynı şekilde fail ettiği doğrulandı;
  bu turun regresyonu değildir.

## Sınırlar

- Lean/lake kurulu değil. Formal kapı davranışı Python tarafındaki kaynak/çıktı
  denetimi üzerinden sınandı; gerçek Lean derlemesi yapılmadı.
- Ücretli LLM sağlayıcı çağrısı yapılmadı.
- POSIX `flock` dalı bu makinede çalıştırılmadı; Windows byte-range lock dalı
  12 gerçek process ile sınandı (tek kazanan doğrulandı).
