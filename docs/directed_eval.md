# Küçük araştırma asistanı değerlendirmesi

Bu araç dışarıda üretilmiş cevapları çevrimdışı değerlendirir. Model çağırmaz, kimlik bilgisi okumaz ve modelin verdiği kodu çalıştırmaz. Mevcut ücretli araştırmaya müdahale etmez.

```powershell
.venv/Scripts/python.exe -m lab.directed_eval list
.venv/Scripts/python.exe -m lab.directed_eval export --output tasks.json
.venv/Scripts/python.exe -m lab.directed_eval score examples/directed_eval/submission.example.json --output evaluation.json
```

Altı sabit görev var: tam sayı kareleri toplamı, modüler ters, bir polinoma karşı örnek, beş adımlık Collatz hesabı, sıfıra bölme hatası denetimi ve noktasal kontraksiyondan uniform kontraksiyona geçiş denetimi. İlk dört görev tam sayı aritmetiğiyle kontrol edilir. Son ikisi insan denetimi için açık rubrik taşır; metin ne kadar ikna edici görünürse görünsün otomatik `PROVED` ya da `EXACT_PASS` almaz.

Örnek dosya bilinen cevaplardan oluşturulmuş sentetik kabul örneğidir. İçindeki sıfır çağrı ve sıfır maliyet herhangi bir modelin ölçülmüş başarısı değildir. Gerçek değerlendirmede her model/effort çifti için ayrı cevap dosyası oluşturun, aynı soruları ve bütçeleri kullanın. Soruları göndermek veya bir ücretli koşuyu başlatmak bu aracın görevi değildir.

`exact_pass_rate` tüm dört exact görevi payda olarak kullanır: eksik cevapları dışlayarak başarı oranını yükseltmez. Rubrik görevleri ayrı `REVIEW_REQUIRED` durumunda kalır. Bu sonuçlar modelin açık araştırma problemlerindeki başarısını, bilimsel yeniliği ya da formal kernel doğrulamasını göstermez. Kolay görevlerin ezberlenmiş olabileceği de hesaba katılmalıdır.

Kullanım ve süre bilgileri dışarıdan gelen, doğrulanmamış metadatadır. Eksik ücret `null`, `cost_complete` ise `false` kalmalıdır. `cost_per_exact_pass_usd` yalnız ücretin tamamı biliniyorsa ve en az bir exact görev geçtiyse hesaplanır. Kısmi timeout maliyetlerini kesin toplam gibi girmeyin. Araştırmacının inceleme süresi ve gerçek bilimsel yarar bu küçük otomatik puandan ayrıca ölçülmelidir.

Yanıt şeması `submission.example.json` içindedir. `suite_id` sabittir; her görev en fazla bir kez bulunabilir. Bilinmeyen görevler, negatif/sonsuz maliyet ve geçersiz token sayıları reddedilir. İnsan değerlendirmesini otomatik exact doğrulama ile karıştırmamak için araç rubrik puanlarını kendiliğinden üretmez.
