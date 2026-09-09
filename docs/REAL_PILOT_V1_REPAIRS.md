# Gerçek pilot V1 sonrası düzeltmeler — 2026-09-07

Çalışma kopyası: `C:/Users/MDP/Documents/Default Project/llm-lab`.

Devam noktası ücretsiz small-b kabulü değil, CP20 XUB gerçek model pilotudur.
İlk pilot PARTIAL kaldı: Sol reasoning bütçesini tüketti, Qwen TIMEOUT oldu,
Grok BLOCKED_DEPENDENCY kaldı. Kullanılabilir bilimsel cevap yoktur.
Eski sonucun kesin kaydedilmiş ücreti $0.061336; raporlanan maliyet aralığı
$0.061336–$0.124898 idi. Bu düzeltmeler eski deneyi yeniden çalıştırmaz veya
eski raporun bilimsel sonucunu değiştirmez.

## Uygulanan düzeltmeler

1. Her çağrı denemesi için rezervasyon çağrıdan önce kalıcı kaydedilir ve
   ücret durumu hemen belirsiz olur. `provider_cost_complete`, ancak bütün
   denemelerin terminal ücret bilgisi varsa true olur. Zaman aşımı, kesinti,
   kullanım cevabı yokluğu veya bir önceki belirsiz retry sıfır ücret sayılmaz.
   Bilinen ücret, çözülmemiş rezervasyon ve toplam aralık ayrı raporlanır.
   Üst aralık sözleşmedeki fiyat tavanlarına bağlı tahmindir; fatura değildir.
   Yinelenen aynı terminal kullanım kaydı iki kez ücret eklemez.
2. Yerel `client_call_id`, ağ çağrısından önce PARTIAL.json'a yazılır.
   Sağlayıcının id/model/provider alanları ilk alındığı anda saklanır.
   Kesilse bile provider_attempts, PARTIAL.json, RESULT.json ve pakette kalır.
   Sağlayıcı hiç olay göndermezse generation ID bilinemez; yerel kimlik kalır.
   Akıştaki ara kullanım verisi tutulur, kesin terminal ücret gibi kaydedilmez.
3. `max_reasoning_tokens` ve `min_final_answer_tokens` sözleşme alanları eklendi.
   Reasoning cap + nominal cevap payı toplam completion tavanını aşamaz.
   Sayısal cap ve effort birlikte kullanılamaz. `reasoning_effort=none` artık
   sağlayıcıya açıkça gönderilir. OpenRouter sayısal cap isteği iletilir;
   diğer adapterlarda sessizce yok sayılmak yerine çağrı öncesi reddedilir.
4. Boş final cevap veya boş findings, finish_reason=stop olsa bile PARTIAL
   sayılır; bağımlı denetçi çalıştırılmaz. Otomatik ücretli tamamlama çağrısı yoktur.

## Açık sınır: nihai cevap garantisi yok

Reasoning cap desteği model ve sağlayıcı rotasına bağlıdır. Effort-only
modellerde sayısal değer bir effort seviyesine çevrilebilir. Bu nedenle
min_final_answer_tokens bir planlama payıdır; kesin cevap uzunluğu veya
gerçek sağlayıcı desteği garantisi değildir. Ön kontrol her model kolunda
provider_support_verified=false ve final_answer_guaranteed=false gösterir.
Eski effort-only sözleşmelerinde bütçenin reasoning ile tükenme riski görünürdür.

İkinci ücretli testten önce baş araştırmacı seçilen model/rota için gerçek
reasoning parametre desteğini doğrulamalı ve yeni sözleşmenin bütçesini
buna göre belirlemelidir. Eski 5.000-token ayarının yeniden denenmesi bu
düzeltmelerle otomatik olarak güvenilir hale gelmez. Bu çalışma kapsamında
ikinci gerçek pilot veya model/API çağrısı başlatılmadı.

Kaynak: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

## Doğrulama

- Canonical çalışma kopyasında directed görev + mimari testleri: 165 passed.
- JUnit: runs/real-pilot-fix-suite.xml.
- Ruff: değişen beş modül ve dört yeni test dosyası temiz.
- Mypy: değişen beş modül temiz.
- Testler yerel sentetik sağlayıcı kullanır; gerçek Sol/Qwen/Grok çağrısı yoktur.
- Pilot biçimindeki PARTIAL + TIMEOUT + BLOCKED_DEPENDENCY tekrar üretildi;
  kimliklerin paket içinde kaldığı ve public verifier'ın geçtiği denetlendi.
- Paket bozulma testindeki tek duplicate-ZIP uyarısı kasıtlı test girdisidir.
- Eski V1 paketinin yeniden okunan SHA-256 değeri değişmedi:
  0835c5365b1ff2a32dc4b75d2d76c89c613f361be51b6f870f06898aecc2d052.

Sonuç: maliyet ve çağrı izleme düzeltmeleri yerel testlerle doğrulandı;
reasoning/cevap bütçesi için kontrol ve raporlama eklendi, model tarafındaki
nihai cevap üretimi henüz canlı testle doğrulanmadı. Bilimsel iddialar OPEN kalır.
