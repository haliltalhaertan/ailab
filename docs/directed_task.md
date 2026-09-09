# Baş araştırmacıdan görev alma

`directed_task`, baş araştırmacının donmuş bir sözleşmeyle verdiği işi yürütür.
Araştırma yönünü baş araştırmacı seçer; bu mod yeni görev üretmez, kapsamı veya
bütçeyi genişletmez, ana araştırma deposuna sonuç yazmaz. Mevcut serbest araştırma
ve basit paralel deney sayfaları ayrı çalışma biçimleri olarak kalır.

## Arayüz

Ücretsiz kabul pilotu için **Ücretsiz küçük-b pilotunu yükle** düğmesine basın.
Bu düğme `examples/directed_pilot_zero/TASK_CONTRACT.json` içindeki yeni v2
sözleşmesini yükler; bütün model çağrı, token ve maliyet tavanları sıfırdır.
Eski `examples/directed_pilot/` sözleşmesi ve önceki teslimleri değiştirilmez:
eski native pilotta nominal pozitif model tavanları bulunsa da model kolu yoktu
ve gerçek model kullanımı sıfırdı. V2 bu sınırı sözleşmede de açıkça sıfırlar.
Bu düğme dört native çalışma kolunu ve yerel girdi klasörlerini forma doldurur;
deney veya model çağrısı başlatmaz. Ardından **Sözleşmeyi doğrula ve planı göster**
ile girdi ve ortam kontrolünü yapın. Ön kontrol uygunsa **Sözleşmeyi dondur ve
çalıştır** ile başlatın. Tamamlandığında claim tablosunu inceleyip sonuç paketini
doğrulayın ve indirin. Bu pilot paralel altyapı ve küçük-b exact kimlikler için
kabul kontrolüdür; ücretli model performansını veya yeni Collatz keşfini sınamaz.

**Directed Task** sayfasında JSON sözleşmesini yükleyin veya tam metin
düzenleyicisinden değiştirin. Çalışma kollarının model, düşünme düzeyi ve fiyat
tavanları ayrıca tabloda düzenlenebilir; uygulama düğmesi bunları sözleşmeye
aktarır. Baş araştırmacının deposunu ve izinli girdilerin kök klasörünü belirtin.

1. `parent_state_commit` gerçek, 40 karakterli araştırma durumu commit'i olmalı.
2. Her girdi, göreli dosya yolu ve SHA256 özetiyle listelenmeli.
3. Önce **Sözleşmeyi doğrula ve planı göster** işlemini çalıştırın. Bu işlem model
   çağrısı yapmaz. Eksik yetenek veya girdi varsa çalıştırma engellenir.
4. Rol, bağımlılık, model ve bütçeleri gözden geçirin. **Sözleşmeyi dondur ve
   çalıştır** arka planda yürütmeyi başlatır. Model kolları gerçek ücretli çağrı
   yapabilir; örnek exact görev model kolu içermez.
5. Proje ve çalışma kimliğiyle ilerlemeyi izleyin. Durdurma isteği, sürdürme,
   paket doğrulama ve ZIP indirme aynı sayfadadır.

Ön kontrol sonrası sözleşme veya klasör yolu değiştirilirse eski planla başlatma
düğmesi kaldırılır. İçe aktarılan dosyanın adı kullanılmaz; her teslim yeni bir
UUID altında `directed_submissions/` içine yazılır. Donmuş bir çalışma JSON
düzenleyicisinden değiştirilemez. Sayfa her yenilendiğinde yeni iş başlatmaz.

`examples/directed_exact_template.json`, ardışık sayı çarpımının çiftliğini iki
ayrı aralıkta kontrol eden, bilimsel yenilik iddiası olmayan bir örnektir. Sıfır
commit alanını gerçek değerle değiştirin. Sözleşmedeki pozitif model bütçeleri
tavandır; native kontrol bu tavana rağmen model çağrısı yapmaz. Yalnız native
kollardan oluşan görevlerde `max_total_llm_calls=0` kullanarak model çağrısını
sözleşmede açıkça yasaklayın; diğer model bütçeleri de sıfır olabilir. Bu, yerel
hesaplamanın duvar saati ve paralellik sınırlarını kaldırmaz. Model kolu bulunan
bir görev sıfır model bütçesiyle başlatılamaz.

Gerçek Collatz araştırma görevinin `parent_repo` alanı baş araştırmacının gerçek
Collatz deposunu göstermelidir. Pilot düğmesinin llm-lab deposunu seçmesi yalnız
altyapı kabulü içindir; bunu bilimsel araştırma durumuna bağlanmış bir teslim
olarak yorumlamayın. `parent_state_commit` yalnız Git HEAD kimliğini bağlar:
commit edilmemiş çalışma ağacını, tüm araştırma evrenini veya bilimsel manifesti
kendiliğinden dondurmaz. Kullanılan bilimsel girdi ve manifestler açık göreli
yolları ve ham SHA256 değerleriyle sözleşmede ayrıca yer almalıdır.

## Sözleşme ve yürütme

Sözleşme sürümü `1.0` kullanılır. Kimlik, objective, primary_claim, tanımlar ve
niceleyiciler; included/excluded kapsamı; izinli/yasak araç ve yöntemler;
girdi manifestleri; çalışma kolları; toplam ve kol bütçeleri; durdurma kuralları
ve sonuç politikası açıkça verilmelidir. Bilinmeyen alanlar sessizce atılmaz.

Bağımlılıklar bir döngüsüz grafik oluşturur. Bağımsız kollar worker sınırına
kadar eşzamanlı çalışır; bağımlı kol izinli öncüllerini bekler. İki kolun farklı
isim taşıması matematiksel bağımsızlık garantisi değildir. Model incelemeleri,
prose ve reasoning tek başına makine doğrulanmış ispat sayılmaz. Üreticilerin
gizli reasoning ve sistem istemleri denetçi girdisine taşınmaz; açık izinli
girdiler ve bağımlılık sonuçları kullanılır.

Native küçük-b pilotunda iki algoritma farklı cebirsel hesap yollarını
karşılaştırır; aynı tanımları, kod tabanını ve koordinatörü paylaşır. Bu,
uygulama içi algoritmik çapraz kontroldür; bağımsız yazılmış iki matematiksel
ispat veya bağımsız formal çekirdek doğrulaması değildir. `small_b_v1` kapalı
pilot reçetesinin kabulü genel bir CAS veya formal ispat yeteneği sağlamaz.

`model` çalışması sabit model kimliği, açık fiyat tavanları ve çıktı sınırı
ister. Fiyat alanları piyasa fiyatını otomatik doğrulayan bir servis değildir;
baş araştırmacı doğru tavanları belirtmelidir. Toplam/kol çağrı, token, maliyet,
duvar saati, tekrar ve paralellik sınırları birlikte uygulanır. Başarısızlıklar
çalışan bir matematiksel iddiaya karşı örnek olarak kaydedilmez.

Native `claim_check` sonlu exact aritmetik kapsamını sınar. Bütün gerçel sayılar
hakkında genel bir cebirsel ispat doğrulayıcısı değildir. `python_exact`,
`symbolic_algebra`, `z3` ve `lean` için sözleşmede belirtilen kaynak ve ortam
gerekir; bulunmayan araç sessizce başka bir araçla değiştirilmez. Bu sürümün
native sembolik kabul yolu yalnız tanımlı küçük-b pilot reçetesine özeldir;
keyfi sembolik Python programını host üzerinde çalıştırma izni vermez. Literatür
kontrolü yalnız açık verilen kaynaklarla sınırlıdır; bu mod kendi başına ağda
kaynak toplamaz.

Directed modundaki Lean adaptörü bu sürümde etkin değildir: Lean bilgisayarda
kurulu olsa bile `lean` kolu `BLOCKED_MISSING_TOOL` ile durur. Kurulumun bulunması
genel formal ispat desteği anlamına gelmez. Native sembolik destek de yalnız
donmuş küçük-b pilot reçetesidir; genel bir CAS arayüzü sunulmaz.

## Sonuç ve epistemik durum

Ledger 1.1 her iddia kimliğini tek satırda birleştirir. Arayüzde kısa tablo
iddia kimliği, ifade ve durumunu gösterir; kapsam ve iç içe destek kayıtları
ayrı açılabilir ayrıntıda yer alır. Böylece çok sayıda kolun aynı iddiayı
desteklemesi tabloda ayrı keşifler gibi görünmez.

İşlem durumu (örneğin tamamlandı, durakladı, engellendi) ile claim durumu
ayrı okunmalıdır. Sayısal tarama `NUMERICAL` olabilir; modelin istediği
`PROVED` etiketi kendi kendine kabul edilmez. `FORMALLY_VERIFIED` için uygun
doğrulayıcı kanıtı gerekir. Beklenmedik fikirler `ESCALATION_CANDIDATE` olarak
baş araştırmacıya iletilir; yeni araştırma başlatılmaz. Küçük-b pilotunun exact
kimlikleri XUB, E6-N2, E7-B4 veya Collatz varsayımını çözmüş sayılmaz.

Teslim paketi sözleşme ve özeti, girdi provenansı, kol sonuçları, claim ledger,
okunabilir bulgular, çalışma/bütçe kayıtları ve bağımsız doğrulama malzemelerini
birlikte taşır. Manifest ve ZIP denetimi dosya bütünlüğünü kontrol eder;
ledger ve okunabilir bulguların birbirleriyle tutarlılığını da denetler.
`VERIFY.py` PASS sonucu matematiksel iddiaların doğruluğunu onaylamaz; birbiriyle
tutarlı fakat matematiksel olarak yanlış içeriğin doğruluğunu salt hash ve
ledger kontrolüyle belirlemek mümkün değildir. Matematiksel iddia ve ispatın
kabulü baş araştırmacının ayrı bilimsel incelemesine bağlıdır. ZIP zaman damgaları
sabitlenir: aynı tamamlanmış kaynak dosyaları aynı paket baytlarını üretir.
Farklı model çağrılarının aynı cevabı vermesi garanti edilmez.

Gizli ayarlar, API anahtarları ve özel checkpoint mühürleri teslim çıktısı
olarak paylaşılmamalıdır. Ana araştırma deposundaki dosyalar salt okunur
girdilerdir. Baş araştırmacı sonuçları inceledikten sonra kanonik kayda hangi
sonucun alınacağına ayrıca karar verir.

## Python arayüzü

`lab.directed` dış kullanıcılar için doğrulama, ön kontrol, başlatma, durum,
durdurma, sürdürme, doğrulama ve paketleme işlemlerini sunar. UI aynı API'yi
kullanır. Uzun işlerde `background=True` kullanın; durum okuması yeni model
çağrısı başlatmaz. Sürdürme mevcut donmuş girdilere ve bütçe kayıtlarına bağlıdır;
girdi veya sözleşme değiştirilmişse yeni görev hazırlayın.

Mevcut worker kullanan entegrasyonlar `lab.worker_launcher.build_directed_request`
ile bir `experiment_method="directed_task"` isteği oluşturabilir. Bu istek
`write_worker_request` / `launch_worker` yoluna verildiğinde worker directed
API'ye eski ajan ve proje kurulumu öncesinde geçer. Bu dal `.env` yüklemez;
model erişimi gerekiyorsa anahtarın çağıran sürecin ortamında önceden bulunması
gerekir. Eski deney yöntemlerinin `.env` davranışı değişmez. Yeni UI doğrudan
directed API'nin arka plan başlatıcısını kullanır.

## Paketi başka bilgisayarda doğrulama

`COMPLETE_PACKAGE.zip` ile yanındaki `PACKAGE_SHA256.txt` dosyasını birlikte teslim edin; SHA256 değerini ayrıca güvenilir kanaldan saklayın. ZIP'i bir klasöre açın, özgün ZIP ve SHA256 dosyasını da aynı klasöre yerleştirin; o klasörde `python VERIFY.py` çalıştırın. Doğrulayıcı kaynak dosyaları, manifest, ledger ve özgün ZIP içeriğini karşılaştırır. ZIP'in kendi hash dosyası döngü oluşturmaması için ZIP dışında tutulur. Arayüzün paket sonucunda hash değeri de gösterilir.

Son yerel kabul sonuçları: `docs/audits/2026-09-07/DIRECTED_TASK_ACCEPTANCE.md`. Sağlayıcının gerçekten gönderdiği `reasoning`, `reasoning_content` ve yapılandırılmış `reasoning_details` alanları kaydedilir; gönderilmeyen içerik üretilmez.

## Reasoning and final-answer output budgets

Model lanes may declare `max_reasoning_tokens` and `min_final_answer_tokens`.
The numerical reasoning cap and final-answer allowance must sum to no more than
`max_completion_tokens`. A numeric reasoning cap cannot be combined with
`reasoning_effort`. A positive final-answer allowance requires a numeric cap or
`reasoning_effort: "none"`; effort settings such as `medium` alone cannot reserve
space for an answer. Existing effort-only contracts remain valid, with an explicit
reasoning-exhaustion warning in preflight.

For example, a 5000-token completion ceiling may request a 3000-token reasoning cap
and a 2000-token final-answer allowance. This is a **nominal allocation**, not a
provider-independent guarantee: provider support is not verified by preflight,
some routes translate a numeric cap to effort, and a model may still return no
usable answer. `min_final_answer_tokens` does not force a minimum answer length.
Preflight exposes each model lane's requested policy, marks enforcement unverified,
and does not automatically start an extra finalization call or expand the budget.
The provider adapter must explicitly transmit `reasoning_effort: "none"` rather
than omitting the setting and leaving provider-default reasoning active.

OpenRouter documents the provider-specific reasoning controls in
[Reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
A real paid pilot is still necessary to assess scientific usefulness after offline
failure-path tests; passing those tests is not a claim that the models will solve XUB.
