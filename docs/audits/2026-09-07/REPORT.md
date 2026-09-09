# PR #37 sonrası yeniden denetim — 7 Eylül 2026

Karşılaştırma: `aae3657` → `3a31136` (main).
Değişiklik: `e9e7347`, "Close 2026-09-06 audit findings A01-A08";
PR #37 ile main'e alınmış. Yerel main fast-forward ile güncellendi.
Üretim kodunda denetçi tarafından ek değişiklik yapılmadı.

**Sonuç:** A03–A08 düzeltmeleri mevcut tekrar/regresyon senaryolarında başarılı.
A01 ve A02 tamamen kapanmamış. Eski formal cache kayıtlarının yeni doğrulama
kapısını atlayabilmesi de giderilmemiş. Aşağıdaki üç sorun tekrar üretildi.

## Kontroller

| Kontrol | Sonuç |
| --- | --- |
| Mevcut güncel testler | 276 geçti, 4 Docker testi atlandı |
| Docker testlerinin gerekli erişimle ayrı denemesi | Yine 4 atlandı; bu sürümde çalıştı diye raporlanmıyor |
| Önceki bağımsız tekrar testleri A01–A07 | 7 geçti |
| A08 | Yeni regresyon testleri JSON/UTF-8 parçalarının birleştirilmesini doğruladı |
| Arayüz açılışları | 12 geçti |
| Ruff | Temiz |
| mypy | 35 kaynak dosyasında temiz |
| Yeni sınır durumları | 3 failure ile aşağıdaki sorunlar doğrulandı |

Eski A08 bağımsız testi, sayfadan yalnız iki fonksiyonu AST ile çıkarıyordu;
yeni `consume_log_chunk` importunu içermediği için bu harness güncel sayfaya
doğrudan uygulanmadı. Bu bir uygulama hatası olarak sayılmadı.

Gerçek Lean derleyicisi ve ücretli provider çağrıları kullanılmadı. Lean
bulguları Python kaynak kapısı/cache davranışı ve oluşturulan probe metni
üzerinden doğrulandı; gerçek Lean ile uçtan uca yanlış PROVEN üretildiği
iddia edilmiyor.

## R01 — P1 — Teorem varsayımları beklenen tipten düşürülebiliyor

Kod: `lab/tools.py:390–394`, `lab/tools.py:514–518`.

Yeni parser, ifadenin kaynakta başka bir yerde geçmesi sorununu gideriyor.
Ancak `_accepted_types`, binder varsa bile yalnız sonucu geçerli bir beklenen
tip olarak kabul ediyor. Örnek:

```lean
theorem bound (h : False) : False := h
```

`theorem_type="False"` ile `draft.ok=True` oluşuyor. Bu teorem koşulsuz False'u
ispatlamaz; yalnız False varsayımı altında False'u verir. Eklenen probe ise
bağımsız beklenen `False` ifadesini değil, kaynaktan yeniden türetilen
`∀ (h : False), False` ifadesini kontrol ediyor. Bu nedenle probe varsayımın
eklenmesini yakalayamaz.

Etki: Bir aday, iddiayı varsayım olarak ekleyip gerçek koşulsuz iddia için
formal başarı metadata'sı elde etme yolunu açık bırakıyor. Son PROVEN etiketi
ayrıca verifier/critic kararlarına bağlıdır.

Gerekli düzeltme: Güvenilen beklenen ifade bütün binder/varsayımları içermeli;
probe aday kaynağından üretilmiş başka bir ifadeye değil tam beklenen tipe
karşı denetlenmeli. Parametreli theorem desteği korunacaksa serbest değişkenler
ve varsayımlar da açık bağlama sözleşmesinin parçası olmalı.

Tekrar: `test_r01_assumptions_cannot_be_dropped_from_expected_type`.

## R02 — P1 — Canlı kilidi taşımak üçüncü worker'a giriş aralığı açıyor

Kod: `lab/integrity.py:268–292`.

Yeni kod iki worker senaryosunda yanlışlıkla taşınmış canlı kilidi geri koyuyor.
Fakat token kontrolünden önce `os.replace(run.lock, moved)` yapıldığı için
bu aralıkta `run.lock` hiç yok. Üçüncü worker O_EXCL ile kilit alabiliyor.
İlk worker geri koymaya çalışınca FileExistsError alıyor ve taşınmış canlı
kilit dosyasını siliyor. İkinci ve üçüncü worker aynı anda sahip kalıyor.

Tekrar testinde deterministik zamanlama:

1. İlk worker eski kilit içeriğini gözlemliyor.
2. İkinci worker eski kilidi devralıp çalışmaya başlıyor.
3. İlk worker ikinci worker'ın canlı kilidini kenara taşıyor.
4. Tam bu boşlukta üçüncü worker yeni kilit alıyor.
5. İlk worker busy dönüyor; ikinci ve üçüncü worker sahip kalıyor.

Gerekli düzeltme: Devralma yolunu, bütün acquire girişlerinin de kullandığı
süreçler arası bir mekanizmayla seri hale getirmek veya süreç ömrüne bağlı
işletim sistemi kilidi kullanmak. Gözlemlenen kilidi taşımak ve sonra kimlik
kontrolü yapmak tek başına yeterli değil.

Tekrar: `test_r02_reclaim_does_not_open_a_window_for_a_third_owner`.

## R03 — P1 — Eski formal cache yeni statement kontrolünü atlıyor

Kod: `lab/theorem_engine.py:809–828`, `lab/theorem_engine.py:849`;
yeni flag: `lab/tools.py` içindeki `theorem_statement_verified`.

Yeni doğrulama flag'i üretildi, ancak formal cache anahtarı hâlâ
`bound_formal_tool:v2`. `_cached_formal_result` yalnız dosyanın varlığını ve
hash'inin değişmediğini kontrol ediyor; yeni flag'i veya güncel kaynak
kapısını aramıyor. İmzalı eski cache kayıtları yükseltmeden sonra da kullanılabilir.

Tekrarda eski A01 örneği kullanıldı: kaynak True ispatlıyor, metadata beklenen
tipi `1 = 2` gösteriyor. Güncel `_guard_source` bu kaynağı reddederken aynı
kaynağa bağlı eski başarılı formal sonuç `_cached_formal_result` tarafından
hâlâ `ok=True` olarak döndürüldü. Bu test eski sonuç biçimini sentetik olarak
kurar; kullanıcının gerçek cache'inde böyle bir kayıt bulunduğunu iddia etmez.

Gerekli düzeltme: Formal doğrulama sürümünü cache ve evidence metadata'sına
katmak; yeni sürümde gerekli statement doğrulaması olmayan sonuçları yeniden
kontrol etmeden kullanmamak. Mevcut kalıcı PROVEN kayıtlarının okunma politikasını
da aynı yükseltme sınırı açısından değerlendirmek.

Tekrar: `test_r03_legacy_formal_cache_is_checked_against_new_statement_gate`.

## Önceki bulguların durumu

| Önceki bulgu | Güncel değerlendirme |
| --- | --- |
| A01 yanlış Lean tipi | İlk örnek giderildi; R01 ve R03 nedeniyle kapanmadı |
| A02 kilit yarışı | İki worker örneği giderildi; R02 nedeniyle kapanmadı |
| A03 bozuk ledger üzerine yazma | Önceki tekrar testi geçti; hata durumunda LedgerReadError var |
| A04 atlanan checkpoint audit | Pending checkpoint kaydıyla önceki hata giderildi |
| A05 kaybolan reasoning_details | Gerçek istemci callback'iyle önceki kesinti testi geçti |
| A06 değişen script için eski cache | Güncel script hash kontrolüyle önceki hata giderildi |
| A07 büyük ağırlık sahte karşıörneği | Sonsuzluk temsiliyle önceki sınır testi geçti |
| A08 parçalanmış JSON/UTF-8 log | Byte buffer ve yeni regresyon testleriyle giderildi |

Bu satırlar belirtilen senaryolardaki doğrulamayı ifade eder; bütün olası
eşzamanlılık ve kesinti kombinasyonları için garanti değildir.

## Dosyalar

- `review_reproductions.py`: Üç açık sorun için beklenen güvenli davranış testleri.
- `reproduction-results.txt`: `3 failed` içeren gerçek çalışma çıktısı.

Normal CI keşfine eklenmemeleri için tekrar dosyaları `test_` ile başlamaz.
Üç tekrarın başarısız olması bu sürümde beklenir. Çalıştırma:

```powershell
.venv\Scripts\python.exe -m pytest docs/audits/2026-09-07/review_reproductions.py -q --basetemp=.venv/review-new-temp
```

`--basetemp` için yeni ve sadece testlere ayrılmış bir klasör seçilmelidir.

## Bağlantı tanısı

Kısıtlı ortamda github.com ve chatgpt.com DNS sorguları zaman aşımına uğradı.
Gerekli ağ erişimiyle github.com çözümlendi, HTTPS 200 ve git fetch başarılı oldu.
Önceki izinli denemenin neden geçici olarak başarısız olduğu kesinleştirilemedi.
Ağ/DNS/VPN ayarı değiştirilmedi.
