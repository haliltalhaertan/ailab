# ailab teknik denetimi — 6 Eylül 2026

İncelenen depo: `haliltalhaertan/ailab`.
Dal: `main`. Commit: `aae36573103ac68a7887d2979515ee87e5f5b558`.

**Sonuç:** Mevcut testler ve statik kontroller geçiyor; fakat bunların kapsamadığı
8 hata ayrı senaryolarla yeniden üretildi. Özellikle formal teorem eşleştirmesi,
eski kilidin devralınması ve okunamayan araştırma kaydının üzerine yazılması
düzeltilmeden uzun süreli araştırmalarda bütünlük garantisine güvenilmemeli.
Bu inceleme sırasında üretim kodu değiştirilmedi.

## Kapsam ve doğrulama

README, bağımlılık ve CI yapılandırmaları; ajan istemcisi ve streaming;
theorem engine, kanıt sınıflandırması ve statü geçişleri; araştırma sözleşmesi;
kalıcı kayıt, SQLite adım önbelleği ve checkpoint/resume;
worker başlatma, kilit, heartbeat ve durdurma; hesaplama araçları ve container
izolasyonu; proje yönetimi, Streamlit sayfaları ve log okuma yolları incelendi.

| Kontrol | Sonuç |
| --- | --- |
| Yerel ortam | Windows, Python 3.14.3; projeye özel `.venv` |
| Kurulum | `pip install -e ".[dev]"` başarılı |
| Mevcut test paketi | 262 geçti, sandbox Docker erişimi nedeniyle 4 atlandı |
| Atlanan gerçek container testleri | Gerekli erişimle ayrıca 4/4 geçti |
| Streamlit açılış testleri | 6 ekran × boş/dolu çalışma alanı = 12 geçti |
| Ruff | `lab tests app.py pages experiments` temiz |
| mypy | `lab` altında 35 kaynak dosyası temiz |
| `pip check` | Bağımlılık çakışması yok |
| Yeni hata tekrarları | Doğru davranışı isteyen 8 assertion mevcut kodda başarısız |
| Canlı ücretli LLM/provider deneyleri | Yapılmadı |
| Gerçek Lean derleyicisi | PATH üzerinde Lean/lake bulunmadığı için çalıştırılmadı |
| Bilinen bağımlılık güvenlik açıkları | OSV sorgusu otomatik onay denetiminde durduruldu; kullanıcı onayı bekliyor |

İlk pytest çalışmasındaki 184 hazırlık hatası, ortak Windows geçici klasörüne
erişim izninden kaynaklandı. `--basetemp` ile proje içindeki yeni bir geçici
klasör kullanıldığında bu hatalar ortadan kalktı; bunlar proje hatası sayılmadı.
Docker 29.3.1 erişilebilir olduğu ayrıca doğrulandı. Dört gerçek container
testi ağ engeli, salt okunur kök dosya sistemi, çıktı yazılabilmesi ve timeout
sonrası container temizliğini kontrol etti.

UI kontrolleri Streamlit 1.63.0 AppTest ile, geçici proje verisi ve sahte model
kataloğu kullanılarak yapıldı. Sayfa açılışlarını doğrular; tarayıcıdaki piksel
düzeni, bütün buton kombinasyonları veya canlı provider çağrıları hakkında
tam kapsamlı garanti vermez. Python 3.10–3.13 CI matrisi bu makinede tekrar
çalıştırılmadı; GitHub'daki güncel CI sonucu da bu raporun doğrulaması değildir.

## Doğrulanmış bulgular

P1: Öncelikle giderilmeli; kanıt doğruluğu, eşzamanlılık veya veri bütünlüğü.
P2: İşlevsel hata; ilgili koşulda yanlış/eksik sonuç veya çalışma kaybı.

### A01 — P1 — Beklenen Lean tipi gerçek theorem bildirimine bağlanmıyor

**Kod:** `lab/tools.py:330–345`, özellikle satır 343.

`_guard_source`, bir adet theorem/lemma adı bulunmasını kontrol ediyor; fakat
`theorem_type` için yalnız tüm kaynak metni içinde alt dize arıyor. Tipin o
theorem'in gerçek tipi olduğunu kontrol etmiyor. Aşağıdaki aday, beklenen tip
`1 = 2` verilmesine rağmen `draft.ok=True` üretti:

```lean
def unrelated : Prop := 1 = 2
theorem bound : True := True.intro
```

Gerçekte ispatlanan ifade `True`; `1 = 2` yalnız başka bir tanımda geçiyor.
Claim hash'inin yorum satırına yazılması bu eşdeğerliği sağlamıyor.
`check_file` aynı kaynak kapısını kullanıyor ve derleyiciden bildirimin tipini
alıp beklenen tiple karşılaştırmıyor. Dolayısıyla derleyici ve axiom kontrolü
başarılı olsa bile yanlış ifadeye formal başarı metadata'sı bağlanabilir;
verifier/critic de kabul ederse PROVEN yoluna ulaşabilir.

**Doğrulamanın sınırı:** Kaynak/draft kapısındaki kabul doğrudan yeniden
üretildi. Gerçek Lean derleyicisiyle uçtan uca yanlış PROVEN deneyi yapılmadı.

**Düzeltme:** Beklenen formal ifadeyi aday kodundan bağımsız sabitlemek ve Lean
ortamında ilgili theorem sabitinin elaborated tipini bu ifadeyle doğrulamak.
Alt dize kontrolünü formal eşdeğerlik kontrolü olarak kullanmamak.

**Tekrar:** `test_a01_lean_binds_actual_theorem_type`.

### A02 — P1 — Eski kilidin temizlenmesi yeni sahibin kilidini silebiliyor

**Kod:** `lab/integrity.py:221–230`.

Kilit okunup eski sahibi ölü bulunması ile `unlink()` ayrı işlemler. İki
worker aynı eski kilidi gördüğünde biri onu silip yeni kilidi aldıktan sonra
diğeri önceki gözleme dayanarak yeni kilidi de silebiliyor. `O_EXCL`, bu
okuma–silme yarışını tek başına korumuyor.

Tekrar testi, ikinci worker'ın kilit almasını ilk worker'ın eski kilit
kontrolü ile silmesi arasına yerleştiriyor. İlk worker `ProjectBusyError`
vermesi gerekirken kilidi alıyor; iki nesne de sahip olduğunu düşünüyor.
Gerçek süreçlerin bu sırayla çalışması aynı proje state/config/SQLite
akışlarının eşzamanlı değişmesine izin verir.

**Düzeltme:** Eski kilit devralma işlemini de süreçler arası kilitle korumak
veya işletim sistemi tarafından süreç ömrüne bağlanan bir kilit kullanmak.
Silmeden hemen önce tekrar okumak tek başına yarışı tamamen çözmez.

**Tekrar:** `test_a02_stale_lock_reclamation_preserves_new_owner`.

### A03 — P1 — Okunamayan araştırma kaydı boş kabul edilip üzerine yazılıyor

**Kod:** `lab/research_state.py:148–152`; yazma yolu `add_item` ve `update_item`.

`_read_state`, JSON veya dosya okuma hatalarında boş `items/events` döndürüyor.
Kayıt gerçekten bozuksa ya da geçici olarak okunamıyorsa sonraki mutation
eski araştırma yokmuş gibi yeni state yazabiliyor. Bu davranış bozuk dosya ile
henüz oluşturulmamış dosyayı ayırmıyor.

Tekrarda mevcut bir kayıt oluşturuldu, dosya yarım JSON haline getirildi ve
bir yeni madde eklendi. İşlem durmak yerine bozuk dosyanın üzerine yalnız yeni
maddeyi yazdı; önceki dosya korunmadı.

**Düzeltme:** Otoritatif ledger okumalarında hata durumunda çalışmayı durdurmak;
boş başlangıcı yalnız gerçekten yeni proje için kullanmak. Kurtarmayı ayrı,
gözlenebilir bir işlem yapmak ve bozuk orijinali korumak.

**Tekrar:** `test_a03_corrupt_ledger_is_not_silently_replaced`.

### A04 — P2 — Yarım checkpoint audit'i resume sırasında atlanıyor

**Kod:** `lab/theorem_engine.py:1582–1599`; resume döngüsü satır 1304.

`completed_iterations`, checkpoint auditor çağrısından önce artırılıyor.
Auditor hataya düşerse çalışma PAUSED_ERROR oluyor; fakat devamda döngü bir
sonraki iteration'dan başladığı için eksik checkpoint'e geri dönülmüyor.

Tekrarda `iterations=1, checkpoint_every=1` kullanıldı. İlk auditor çağrısı
hata verdi. İkinci çalıştırma COMPLETED oldu, ancak
`iter:1:checkpoint_audit` önbellek kaydı hâlâ yoktu. Final audit'in çalışması,
istenmiş olan ara checkpoint denetiminin tamamlandığı anlamına gelmiyor.

**Düzeltme:** Iteration tamamlanmasını gerekli checkpoint adımından sonra
işaretlemek veya checkpoint için ayrı kalıcı cursor/pending adımı tutup
resume başlangıcında eksikleri tamamlamak.

**Tekrar:** `test_a04_interrupted_checkpoint_is_resumed`.

### A05 — P2 — Yapılandırılmış reasoning kesintide kayboluyor

**Kod:** `lab/client.py:427–432`; tüketici `lab/theorem_engine.py:361–380`.

İstemci `reasoning_details` parçalarını yerel listeye ekliyor; `stream_callback`
üzerinden iletmiyor. Theorem engine bu kanalı alırsa partial kayda yazabilecek
durumda, fakat gerçek istemciden kanal hiç gelmiyor. Yanıt tamamlanmadan bağlantı
kesilirse istemcinin yerel listesi de sonuç nesnesi olarak dönmüyor.

Tekrarda yalnız bir yapılandırılmış reasoning parçası geldi ve ardından
bağlantı hatası oluşturuldu. Partial kaydı tamamen `None` kaldı. Bu tür
akışlarda devam bağlamı kaybolur; DURDUR kontrolü de content/reasoning callback'i
gelene kadar tetiklenmeyebilir.

Mevcut `test_client_stream.py`, ayrıntıların callback'e verilmemesini özellikle
bekliyor; bu test davranışının partial/resume gereksinimiyle uzlaştırılması
gerekiyor. UI'da aynı metni iki kez göstermemek ile persistence kanalını
tamamen kapatmak farklı konular.

**Düzeltme:** Yapılandırılmış parçaları persistence/cancel kanalına iletmek,
gerekirse UI metin kanalından ayırmak; kesinti halinde flush edildiğini test etmek.

**Tekrar:** `test_a05_structured_reasoning_survives_interruption`.

### A06 — P2 — Checker kodu değişse de eski araç sonucu kullanılıyor

**Kod:** `lab/theorem_engine.py:917–927`.

Genel araç fingerprint'i yalnız istek JSON'undan oluşuyor. Script dosyasının
hash'i sonuç metadata'sında bulunmasına rağmen cache okunurken güncel kaynakla
karşılaştırılmıyor. Kontrol script'i hata düzeltmesiyle güncellendiğinde aynı
adım eski başarılı veya başarısız kanıtı yeniden kullanabilir.

Tekrarda güvenilir geçici script önce `version-one`, sonra `version-two`
yazacak şekilde değiştirildi. İkinci aynı araç isteği hâlâ `version-one`
döndürdü. HMAC cache'in değiştirilmediğini doğrular; checker'ın hâlâ aynı
kod olduğunu doğrulamaz.

**Düzeltme:** Güncel checker/script hash'ini ve ilgili yürütme sürümünü cache
anahtarına dahil etmek; değişmiş kod için eski kanıtı tekrar kullanmamak.

**Tekrar:** `test_a06_tool_cache_invalidated_when_script_changes`.

### A07 — P2 — Büyük geçerli ağırlıklar sahte karşıörnek üretiyor

**Kod:** `lab/tools.py:568–582`, özellikle `inf = 10**18`.

Tropical referans algoritmasında sonsuzluk yerine sonlu bir sayı kullanılıyor.
Arayüz bu sınırdan büyük nonnegative tamsayıları reddetmiyor. Tek kenarlı,
doğru bir `n=2` devresinde ağırlık `10**18 + 1` verilince beklenen değer yanlış
biçimde `10**18` kaldı ve araç COUNTEREXAMPLE üretti.

Bu sonuç evidence/status yolunda deterministik karşıörnek kabul edilerek
doğru adayın FAIL durumuna düşmesine neden olabilir.

**Düzeltme:** Gerçek sonsuzluk veya erişilmemiş mesafe için ayrı bir temsil
kullanmak; kabul edilen tüm tamsayı aralığında doğru referans hesaplamak.

**Tekrar:** `test_a07_large_valid_weights_do_not_create_false_counterexamples`.

### A08 — P2 — Canlı ham log okuyucusu bölünmüş JSON satırını bozuyor

**Kod:** `pages/2_Ham_Loglar.py:67–82`.

Okuma sonunda offset doğrudan EOF'a taşınıyor; son eksik satır sonraki okumaya
saklanmıyor. Worker yazarken okuyucu bir JSON satırının ortasına denk gelirse
iki yarı ayrı INVALID_JSON olayları olarak kalıyor. Ham metin birikse de
olay listesi yeniden birleştirilmiyor. UTF-8 karakteri byte sınırında
bölünürse ayrı decode işlemleri metni de bozabilir.

Tekrarda tek geçerli `agent_start` satırı dosyaya iki parçada yazıldı. Okuyucu
bir geçerli olay yerine iki INVALID_JSON kaydı üretti.

**Düzeltme:** Yalnız son newline'a kadar tüketmek ve offset'i orada tutmak;
mevcut `lab.ui_model.read_jsonl_since` yaklaşımını paylaşmak.

**Tekrar:** `test_a08_raw_log_tail_preserves_split_json_records`.

## Teslim edilen kanıtlar ve tekrar çalıştırma

- `audit_reproductions.py`: Sekiz bulguyu doğrulayan, beklenen doğru davranışı
  assertion olarak ifade eden senaryolar. Mevcut commit'te 8 failure beklenir.
- `reproduction-results.txt`: Son tekrar çalışmasının gerçek çıktısı.
- `audit_ui_checks.py`: On iki geçen sayfa açılış kontrolü.
- `environment.txt`: Denetimde kurulan paket sürümleri.
- `check_advisories.py`: Henüz çalıştırılmamış, açık onay gerektiren OSV sorgusu.

Tekrar dosyalarının adları standart `test_*.py` kalıbında değildir; olağan
pytest/CI keşfine otomatik eklenmezler. İstenirse açık dosya yolu ile çalışırlar.
Geçici klasörlerde yalnız sentetik araştırma kayıtları ve zararsız test
script'leri oluştururlar. Gerçek kullanıcı projesi veya ücretli LLM çağrısı yoktur.

```powershell
# Her çalıştırmada yeni, yalnız bu testlere ayrılmış bir basetemp yolu seçin.
.venv\Scripts\python.exe -m pytest tests -q --basetemp=.venv/audit-new-baseline
.venv\Scripts\python.exe -m pytest docs/audits/2026-09-06/audit_ui_checks.py -q --basetemp=.venv/audit-new-ui
.venv\Scripts\python.exe -m pytest docs/audits/2026-09-06/audit_reproductions.py -q --basetemp=.venv/audit-new-repros
```

`pytest --basetemp` var olan hedef klasörü temizleyebilir; mevcut araştırma
klasörünü bu parametreye vermeyin.

## Düzeltme sırası

1. A01, A02, A03: formal bağlama, kilit devralma ve ledger okuma bütünlüğü.
2. A04, A05, A06: checkpoint/resume, reasoning kaydı ve araç cache doğruluğu.
3. A07, A08: hesaplama sınır durumu ve canlı log bütünlüğü.

Her düzeltmede ilgili tekrar assertion'ı geçirilip mevcut testler korunmalı.
Geçen unit testler, gerçek provider/Lean doğrulamasının veya bütün sistem için
eksiksiz güvenlik garantisinin yerine geçmez.
