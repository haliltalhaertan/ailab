# Baş araştırmacı kabul bulguları — düzeltme kaydı

Tarih: 2026-09-07. Baş araştırmacının ACCEPT_WITH_LIMITATIONS değerlendirmesindeki altı bulgu uygulandı.

| Bulgu | Yapılan değişiklik |
|---|---|
| Claim tekrarı | Ledger 1.1: her claim_id tek satır; lane çıktıları supporting_evidence altında. Aynı ID ile farklı ifade, durum, domain veya varsayım varsa INCONCLUSIVE. Çoğunluk oylaması yok. |
| Bağımsızlık | Konsolide iddialar SHARED_TRUST_BASE olarak etiketlenir; tanımlar, kod ve koordinatör paylaşımı belirtilir. |
| Sembolik kapsam | UI ve preflight small_b_v1 sınırını açıkça gösterir; genel CAS/formal kernel iddiası yok. |
| Nominal bütçe | Model içermeyen görevlerde gerçek sıfır çağrı/token/maliyet bütçesi desteklenir. Yeni small-b-native-pilot-zero-v2 ayrı kimlikle oluşturuldu; eski sözleşme korunur. Eski native sözleşmelerin etkin model tavanı da sıfır gösterilir. |
| Parent bağlamı | UI ve CAPABILITY_REPORT: parent commit HEAD_ONLY, dirty çalışma ağacını dondurmaz; gerçek Collatz repo/commit ve bilimsel input/manifest hashleri ayrıca gerekir. |
| Doğrulayıcı sınırı | UI'da sürekli görünür: byte bütünlüğü ve ledger/Markdown uyumu matematiksel hakikat doğrulaması değildir. |

Ek denetimde, dar bir domain için elde edilen sonucun daha geniş primary claim'e taşınmasını önlemek üzere domain/varsayım bağlama kontrolleri eklendi. Çelişki veya eksik ispat bağında otomatik yükseltme yapılmaz.

## Doğrulama

- Directed, UI ve mimari testleri: 125 passed, 29.25 s. Rapor: runs/principal-feedback-tests.xml.
- Ruff: temiz. Mypy: 47 modülde hata yok.
- Yeni pilot: directed-b43c155ecb824034bb87d64778230f24; COMPLETED_WITH_OPEN_CLAIMS.
- Beş tekil claim: A0, A1, EQUALITY, GENERAL-MODULUS PROVED; XUB OPEN.
- Yeni model çağrı/token/maliyet sınırları: 0 / 0 / 0. Model çağrısı yapılmadı.
- Paket doğrulama: PASS. Tekrar paketleme SHA256 değerini değiştirmedi.
- Baş araştırmacının eski bağımsız paketi hâlâ doğrulanıyor: a08916915dc846f6d71aa32323ceed9e17db57a81232d0dc1abbfd9bca2bcde0.
- Eski 1.0 paketleri için yalnız bilinen verifier baytları allowlist ile destekleniyor; 1.1 paketleri eski verifier'a düşürülemiyor. Eski paket tekrar paketlendiğinde de baytları korunuyor.

Yeni paket: C:\Users\MDP\Documents\Default Project\llm-lab\research_state\directed-native-pilot-zero\directed\directed-b43c155ecb824034bb87d64778230f24\package\COMPLETE_PACKAGE.zip

SHA256: 069ce4460139a2709b857c4810a4b9a90c54db668ec392481d177e1f45862204

Yeni sözleşme: examples/directed_pilot_zero/TASK_CONTRACT.json. Ücretsiz pilot düğmesi bu sözleşmeyi yükler. Eski examples/directed_pilot/TASK_CONTRACT.json değiştirilmedi.

Bilimsel sonuç kapsamı temel cebir ve exact katsayı çapraz kontrolüdür. Formal kernel, yeni keşif veya Collatz/XUB ispatı değildir. ACCEPT_WITH_LIMITATIONS değerlendirmesinin araştırma kapsamına ilişkin sınırları devam eder; bu kayıt geliştirici düzeltmelerinin ve yerel testlerinin kanıtıdır.
