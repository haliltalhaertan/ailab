# Görev hazırlama ve baş araştırmacı iş akışı

## Arayüz

**Yeni çalışma** sayfasındaki form, mevcut directed_task sözleşmesini üretir.
Görev türleri: ispat adımı denetimi, bağımsız türetim, karşı örnek arama, sonuç
karşılaştırma ve sonlu tam sayı kontrolü. JSON girmek gerekmez; ileri düzey
sözleşme düzenleyicisi ve eski çalışma yolları erişilebilir kalır.

1. Görev/proje/baş araştırmacı kimliğini, işi, iddiayı ve beklenen teslimi belirtin.
2. Git deposunu, girdi klasörünü ve açık göreli dosya yollarını belirtin.
   Sadece seçilen dosyalar SHA-256 ile bağlanır. HEAD, dirty working tree'nin
   tamamını temsil etmez. Başka araştırmanın deposunu seçmeyin.
3. Model kimliği ve fiyat/bütçe tavanlarını belirtin. Denetçi seçilirse üretici
   sonuçlarını bekler; ham reasoning paylaşılmaz. Bu sürümde kollar aynı model
   kimliğini kullanır; farklı modeller için ileri sözleşme düzenleyicisini kullanın.
4. Taslağı hazırlayın ve görüntülenen kapsam/bütçeyi inceleyin.
5. Kuyruğa ekleyin. Bu işlem çalıştırmaz.
6. **Çalışmalar → Görev kuyruğu** içinde seçili görevi açıkça başlatın.
7. Listeyi yenileyin. Kolun reasoning, cevabı, sonucu, teslim kontrolü ve insan
   değerlendirmesi aynı çalışma ekranındadır. Hazır ZIP bu ekrandan alınabilir.

Sonlu tam sayı kontrolü model çağrısı yapmaz. Üretilen primary_claim serbest
metinden değil, gerçekten çalıştırılacak koşul ve aralıktan kurulur. Model
görevlerinde bir kol bir çağrı yapar; otomatik yeniden deneme veya yeni görev yoktur.

Sayısal reasoning sınırı ve nihai cevap payı mevcut sözleşme politikasına aktarılır.
Desteklenmeyen adaptör/ayar ön kontrolde engellenir. Yalnız low effort seçmek nihai
cevap için bir token rezervasyonu garantisi değildir.

## Baş araştırmacı bağlantısı

[Yerel kuyruk API ve CLI kılavuzu](principal_queue.md) gönderim, sorgulama ve açık
başlatma komutlarını içerir. Sunucu veya otomatik zamanlayıcı kurulmaz. Kuyruk
kalıcı SQLite kullanır; eşzamanlı gönderim ve başlatma aynı işi çoğaltmaz. Aynı
anahtar farklı baytlara bağlanamaz. Girdiler dispatch sırasında yeniden kontrol
edilir. Belirsiz başlatma sonucu otomatik tekrar edilmez.

## Teslim ve bilimsel değerlendirme

`lab.delivery_check.build_delivery_check(run_folder)` yalnız kaydedilmiş teslimin
yapısal bütünlüğünü denetler: ilan edilen kollarda tamamlanmış cevap ve gerekli
paket dosyaları var mı? Reasoning tek başına cevap değildir.

- `COMPLETE`: yapısal teslim tamam.
- `PARTIAL`: eksik, bozuk veya güvenle okunamayan teslim var.
- `PENDING`: teslim bekleniyor.
- `scientific_validation=NOT_PERFORMED`: matematiksel doğruluk denetlenmedi.
- `human_review=NOT_ASSESSED`: bu kontrol insan yararlılık kararı vermez.

Serbest metin olarak belirtilen her teslim beklentisinin anlamsal karşılandığını
otomatik doğrulamaz. OPEN iddia, tamamlanmış ve yararlı bir teslimde bulunabilir.
İnsan değerlendirmesi için önce sabit sonuç görüntüsü yüklenir; sonuç değişirse
eski görüntüye göre verilen değerlendirme kaydedilmez. İnceleme kaydı donmuş
paketin dışında tutulur.

## Doğrulama

- Builder, kuyruk, teslim kontrolü, değerlendirme ve mevcut UI kabul testleri.
- Eşzamanlı aynı gönderim/dispatch ve belirsiz başlatmadan sonra tekrar engeli.
- Taslak hash uyuşmazlığı ve incelemeden sonra sonuç değişimi engeli.
- Gerçek native motorla builder → kuyruk → hesaplama → teslim uçtan uca testi;
  sıfır model çağrısı. Bu testte dış Git kimliği fixture ile sağlanır.
- Windows symlink izin testi ortam nedeniyle atlanabilir; path escape ve
  bozuk veri testleri ayrıca çalışır.

Bu geliştirme modeli otomatik seçme, genel CAS/Lean veya otonom araştırma
özelliği eklemez. Mevcut görevlerin güvenilir hazırlanması, teslimi ve değerlendirilmesi
üzerine kuruludur.
