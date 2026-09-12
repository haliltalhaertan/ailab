# Muse Code bağımsız denetimi — 12 Eylül 2026

**Sonuç: CHANGES_REQUIRED.** Bu kayıt, önceki RECOVERY_AND_PROOF.md raporundaki
test kapsamını tamamlar; bütün worker yollarında bozuk runtime'ın korunduğu
yönündeki geniş ifade geçerli değildir.

## Çalıştırma ve bütünlük

Muse Code 1.1.1, Ubuntu/WSL üzerinde ayrı ve dondurulmuş kaynak kopyasında
çalıştırıldı. Gerçek araştırma kayıtları, .env, erişim anahtarları, .git ve
sanal ortam kopyaya alınmadı. Yazma aracı ve web araçları kapalıydı; shell
sandbox açık kaldı. Sentetik problar yalnız ayrı scratch dizininde çalıştı.

- Muse exit code: 0; süre 312.77 saniye.
- 36 model adımı, 49 tamamlanan araç çağrısı.
- Snapshot'taki 286 dosyanın tamamının SHA-256 değeri koşu sonunda değişmemişti.
- Gerçek Windows çalışma kopyalarındaki aynı 286 dosya da tekrar karşılaştırıldı:
  değişen dosya yok.
- Muse'un bildirdiği 7 prob dosyası diskte bulundu ve okundu/kopyalandı.
- Muse session: 01a095d2-aaf3-7c61-ace5-cad2a8231b87.
- Bu tur uygulama kaynaklarına düzeltme uygulanmadı.

## Windows'ta iki kopyada da yeniden üretilen bulgular

### P1 — ProjectManager strict runtime kontrolünü atlıyor (Muse bulgusu)

lab/project_manager.py:203, _write_runtime_status, tolerant JSON okumasıyla
bozuk runtime'ı boş sözlük sayıp yeniden yazıyor. Worker başlangıcındaki ve
finally bloğundaki pm.touch(status=...) çağrıları bu yola giriyor.

Doğrulama: runtime içinde 7 tamamlanmış tur ve sıradaki görev varken dosya
bozuldu. RUNNING touch bozuk baytları yeni durum kaydıyla değiştirdi; ilerleme
alanı kayboldu. mark_runtime_error bozuk baytları korudu, fakat sonrasındaki
PAUSED_ERROR touch yeniden üzerine yazdı. Her iki Windows kopyasında aynı.

Önceki koddan kalan, son düzeltmenin kapsamadığı bir bypass. Öncelikli onarım:
ProjectManager dahil bütün runtime writer'larının aynı strict okuma ve
eşzamanlılık sözleşmesini kullanması; worker başlangıç/finally regresyonları.

### P1 — Araştırma aşaması güncellemesi worker ilerlemesini geri alabiliyor (yerel bağımsız bulgu)

lab/run_controller.py:107, modül düzeyindeki set_research_phase bir
read-modify-write işlemi yapıyor; diğer runtime writer'larıyla ortak kilidi yok.
Deterministik araya-girme probunda helper 7 turu okuduktan sonra worker 8 tur ve
yeni görevi yazdı. Helper ardından eski sözlüğü kaydetti: 8 -> 7, yeni -> eski görev.
Bu prob zamanlamaya dayalı bir tahmin değil; iki yazmanın sırası kontrollü kuruldu.

Öncelikli onarım: faz değişimi dahil runtime yazımlarını tek writer veya ortak
kısa süreli süreçler arası mutation kilidi üzerinden yürütmek. Salt atomik
dosya değiştirme, read-modify-write kayıp güncellemesini engellemez.

### P2 — Mevcut runtime tamamen kaybolursa yeni çalışma sayılıyor (yerel bağımsız bulgu)

lab/run_controller.py:63, read_runtime FileNotFoundError için default_runtime
döndürüyor. Yeni proje için bu gerekli, fakat daha önce çalışmış projede dosya
silinince de aynı yol kullanılıyor. Prob: 7 tur -> dosya kaybı -> set_runtime ->
0 tur ve boş next_task.

Öneri: ilk başlatma ile önceki runtime'ın kaybını ayıran kalıcı başlangıç/çalışma
kimliği; kayıp kayıtta otomatik varsayılanla devam yerine açık kurtarma.

### P2 — Silinen snapshot yeni bağlamla yeniden üretilebiliyor (yerel bağımsız bulgu)

lab/theorem_engine.py:1021, _iteration_snapshot, kayıt yoksa yeni snapshot
oluşturuyor. Önceden dondurulmuş tur satırını silip ledger bağlamını değiştirdik;
aynı tur için farklı ledger revision ve farklı next_task kabul edildi.
İmza değiştirilmiş satırı yakalıyor, tamamen kaybolan satırı yeni turdan ayıramıyor.

Öneri: daha önce başlatılmış turun snapshot varlığını ayrı, güvenilir cursor
veya manifest ile bağlamak; eksik devam referansında durmak.

## Kısmen doğrulanan formal kontrol bulgusu

Muse'un Lean context bulgusu için kaynak önkontrolünü Windows'ta yeniden
ürettik: notation "1 = 2" => True ardından theorem bound : 1 = 2 := trivial
kaynağı _guard_source tarafından kabul edildi.

**Bu yalnız kaynak önkontrolünün sonucudur. Gerçek Lean kernel koşusu yapılmadı;
yanlış bir matematiksel önermenin PROVEN'a uçtan uca ulaştığı gösterilmedi.**
Ek notation/definition bağlamının statement probe'u da etkileyebilmesi
incelenmelidir. Tüm def/open kullanımını körlemesine yasaklamak yerine
güvenilir formalizasyon bağlamı ve bağımsız statement kontrolü tasarlanmalıdır.

## Muse raporuna uygulanan değerlendirme

- Ledger API'sinin doğrudan çağrılmasının manager/verifier/critic istemediği
  gözlemi, tek başına yeni güvenlik açığı kabul edilmedi. Normal engine guard'ı
  bunları denetler; trusted Python caller ayrıca bir sınırdır. Mevcut son rapor
  ledger read/write için exact-match kontrolünü vaat ediyor, manager receipt'i
  ayrıca vaat etmiyor.
- research_phase için geçersiz stringlerin okunabilmesi küçük bir doğrulama
  eksikliği olarak not edildi; ana öncelik ilerleme kaybı yollarıdır.
- Yerel HMAC anahtarını bilen yöneticinin imza üretebilmesi ve doğal dil
  çevirisinin otomatik doğrulanmaması önceden belgelenmiş sınırlardır.
- Mevcut bozuk JSON, değiştirilmiş snapshot/partial, SQL sütun eşleşmesi,
  bağlantı kapanması ve PROVEN metin eşleşmesi kontrolleri kendi dar
  kapsamlarında ayakta kaldı. Geniş regresyon testlerinin yeşil olması
  yukarıdaki kapsanmayan yolları kapatmıyor.

## Yeniden üretim

Bu raporun yanındaki muse_review_probes.py yalnız geçici sentetik projelerde
çalışır. Windows'ta ilgili çalışma kopyasının Python'u ile çalıştırın ve
argüman olarak denetlenecek repo kökünü verin. Kanıt çıktıları
MUSE_WINDOWS_RESULTS.json içindedir.

Muse'un ham raporu ve terminal çıktısı, ana çalışma kopyasında
runs/muse-audit-20260912/muse-run.log altında korunmuştur. Aynı dizinde manifest,
completion.json, snapshot.zip, prompt ve Muse'un yedi probu bulunur.
