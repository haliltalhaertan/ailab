# Yerel düzeltme ve doğrulama — 2026-09-07

Temel sürüm: `3a31136` (PR #37 sonrası). Değişiklikler yerel çalışma ağacında.

## Düzeltilen bulgular

- R01: Binder içeren Lean declaration için theorem_type artık tüm parametre ve varsayımları `∀ <binders>, <conclusion>` olarak içermek zorunda. Derleyici probe'u doğrudan beklenen theorem_type ile oluşturuluyor. Ajan talimatı da bu kurala uyarlandı.
- R02: Proje çalışması boyunca kalıcı `.run.guard` dosyası üzerinde OS kilidi tutuluyor (Windows byte-range lock, POSIX flock). `run.lock` yalnız sahiplik metadata'sı; canlı kilidi taşıyan reclaim yarışı kaldırıldı. Guard dosyası silinmemeli. İşlem kapanınca işletim sistemi kilidi serbest bırakır.
- R03: Formal doğrulama sürümü 2 ve theorem_statement_verified zorunlu. Formal önbellek anahtarı v3. Evidence sınıflandırma, PROVEN yükseltmesi, kayıt okuması ve HMAC payload aynı sürüm kontrolünü uyguluyor. Eski kaydın okunması kaynak dosyayı değiştirmeden PROOF_CANDIDATE görünümü verir; yeni doğrulama gerekir.

## Doğrulama

- Tam test paketi + üç bağımsız tekrar testi + 12 Streamlit AppTest senaryosu: **297 geçti, 4 Docker testi atlandı** (64.11 saniye).
- Sonradan eklenen iki legacy kayıt/HMAC testi dahil formal uçtan uca dosya: **7 geçti** (önceden sayılan beş test dahil).
- Docker Desktop başlatıldıktan sonra dört gerçek konteyner izolasyon testi: **4 geçti** (10.07 saniye). Böylece farklı turlarda toplam **303 ayrı test başarılı**; son durumda Docker atlaması kalmadı.
- Ruff: temiz. Mypy: 35 kaynak dosyası temiz. Git diff whitespace kontrolü: temiz.
- Yeni regresyonlarda gerçek ayrı Python işlemleri kullanıldı: metadata silinse de ikinci sahip engelleniyor; süreç ölümü sonrası kilit devralınabiliyor.
- Yerel Streamlit sunucusu: `http://127.0.0.1:8501`; sağlık cevabı `ok`. Ana sayfa ve Reasoning Settings tarayıcıda görüntülendi.

## Sınırlar

- Lean/lake kurulu değil. Derleyici çağrı akışı kontrollü test doubles ile sınandı; gerçek Lean derlemesi yapılmadı.
- Gerçek ücretli LLM araştırma çağrısı yapılmadı. Yerel `.env` dosyası yok.
- Windows OS kilidi gerçek süreçlerle sınandı; POSIX dalı bu bilgisayarda çalıştırılmadı.
- Önceki REPORT.md ve reproduction-results.txt dosyaları düzeltme öncesi bulguların tarihsel kaydıdır.
