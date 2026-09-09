# Araştırma yardımcısı geliştirmeleri — 2026-09-08

## Reasoning kaydı

Her küçük akış parçasında bütün geçmiş tekrar diske yazılmak yerine,
PARTIAL.json en fazla saniyede bir yenilenir. Yerel çağrı kimliği, sağlayıcı
kimliği, kullanım verisi, normal tamamlanma ve yakalanan kesinti anında
anında kayıt yapılır. TRACE olay sayaçları korunur. Sürecin zorla öldürülmesi
veya elektrik kesintisinde son snapshot'tan sonraki yaklaşık bir saniyelik
metin kaybolabilir; bu bir işlem günlüğü dayanıklılık garantisi değildir.

Hassas metin maskelemesi, her iç metin için ortam değişkenlerini yeniden
taramak yerine her kayıt işlemi için tek ortam görüntüsü kullanır. Her yeni
kayıtta yeniden alınır; değişen kimlik bilgileri eski önbellekte kalmaz.
Mevcut bir kayıtta eski ve yeni sonuçlar birebir karşılaştırılmıştır.
Ölçüm yalnız maskeleme süresidir; sağlayıcı üretim hızı değildir.

## Canlı görevler

pages/8_Canli_Gorevler.py ana uygulamanın yeni sayfasıdır. Kayıt kökleri
eklenebilir; root/project/directed/run yapısındaki çalışmalar listelenir.
Reasoning, cevap ve sonuç sekmeleri salt okunurdur. Ekran yeni görev veya
ücretli çağrı başlatmaz. Kısa araştırma raporu ve model/effort ölçüleri
aynı sayfadan incelenir. Tam metin indirmeleri mevcut kayıtları kopyalar;
donmuş deney paketlerini yeniden yazmaz.

## Değerlendirme

lab.directed_eval altı bilinen görev için çevrimdışı karşılaştırma sağlar.
Dört sonlu görev exact kontrol edilir, iki ispat denetimi insan incelemesi
bekler. Sentetik örnek model başarısı değildir. Kullanım ve ücret verisi
yoksa sıfır veya kesin toplam varsayılmaz. Ayrıntı: directed_eval.md.

## Etkin deneyler

Bu değişiklikler ana llm-lab kopyasına uygulanır. Daha önce dondurulup ayrı
runtime kopyasıyla başlatılmış deneylerin kodu veya bütçesi değiştirilmez.
Canlı izleme eski kayıtları da okuyabilir; hız düzeltmesi yeni çalıştırmalarda
etkilidir. Eski deneyin kod mühürlerini aşarak resume yapılmamalıdır.
