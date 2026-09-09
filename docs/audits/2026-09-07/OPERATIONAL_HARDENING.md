# Operasyonel iddia doğrulama — 2026-09-07

Asıl kurulum: C:/Users/MDP/Documents/Default Project/llm-lab
Yerel dal: codex/local-audit-fixes. Değişiklikler henüz commit edilmedi.

## Uygulanan değişiklikler

- claim_spec: değişken aralıkları, varsayımlar ve tam predicate tek bir normalleştirilmiş ifadede sabitleniyor. Aynı kayıt bu alanları değiştiremiyor.
- recheck_item_id: eski iddianın tanımı aynen kullanılmalı. Eski iddianın tanımı yoksa veya farklılaştırılırsa kanıt taşınmıyor; ayrı hipotez gerekiyor.
- claim_check: serbest kaynak kodu çalıştırmayan, sınırlandırılmış kesin aritmetik denetleyici. Gerçek karşıörnek, özgün predicate üzerinde hesaplanıyor. Boş/kapsam dışı/bütçesi biten kontroller INCONCLUSIVE.
- Operasyonel denetim daha geniş bir ResearchContract hedefini yalnız hedef ID'si veya doğal dil benzerliğiyle kapatamıyor.
- Başarılı generated code_experiment artık yalnız çalışmanın başarısı; tek başına COMPUTATION_PASS üretmiyor.
- Yerel kaynak/specifikasyon hatasında üç model hakem çağrısı atlanıyor. Aynı hata ikinci ayrı adayda tekrar ederse duraklatılıyor.
- Modelin hatalı claim_spec çıktısına bir odaklı düzeltme fırsatı veriliyor; ikinci hata deneye geçmeden duraklatılıyor.
- Tam final ve ara denetim metni saklanıyor; 1000/1500 karakter kesmeleri kaldırıldı. Model incelemesi bağımsız matematiksel doğrulama diye adlandırılmıyor.
- Ajan bağlamında tam operasyonel ifade ve makine kontrolünün kapsam/örnek özeti korunuyor. Yeni adayların novelty_status alanı UNASSESSED.

## Test kanıtı

1. Yeni regresyon dosyası: 39 test geçti. Gerçek son Collatz koşusundaki anlam kayması, hatalı karşıörnekler, eski hedefe haksız kanıt transferi, sınırsız işlemler, boş kapsam, değiştirilen kayıt ve düşük kaliteli model çıktıları dahil.
2. Asıl ortamın tüm testleri + 12 arayüz senaryosu: ilk turda 366 geçti, 3 test yalnız eski rapor başlığını beklediği için başarısız oldu. Başlık beklentileri güncellendi; ilgili üç test 6.73 saniyede geçti. Toplam 369 ayrı kontrol başarılı duruma geldi; Docker testleri dahil, atlama yok.
3. Son prompt düzeltmesinden sonra operasyonel iddia/semantik aktarım/araç kullanılabilirliği grubu: 56 geçti (15.05 saniye).
4. Ruff temiz, mypy 37 kaynak dosyasında temiz, diff whitespace kontrolü temiz.
5. API çağrısız karşılaştırma deneyi: 8/8 beklenen sonuç. JSON: runs/local-validation-20260907/operational-benchmark.json.

## Bilimsel sınır

Bu testler bilimsel keşif oranını veya ucuz bir LLM'nin araştırma performansını ölçmez. Yeni ücretli LLM çağrısı yapılmadı. Doğal dilden operasyonel ifadeye çevirinin doğruluğu ayrıca incelenmelidir. Yeni mekanizma yalnız desteklenen exact arithmetic ve sabit işlevler için hesaplamayı güvenceye alır. Gerçek veri, birimler, istatistik ve başka bilim alanları için incelenmiş alan denetleyicileri gerekir. Sonlu test ve model raporu ispat veya özgünlük belgesi değildir. Lean/lake kurulu değil.

Eski araştırma kayıtları değiştirilmedi. Önceki kaynak dosyalarının yedeği runs/before-operational-claims-20260907 altında. Kullanım ve modelin kullanacağı örnek şema docs/operational_research.md dosyasında.
