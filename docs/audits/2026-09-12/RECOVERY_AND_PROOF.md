# Kurtarma ve kanıt bütünlüğü — 12 Eylül 2026

Bu güncelleme AILab ve günlük kullanılan LLM Lab çalışma kopyalarına uygulanmıştır.
Kapsam: durum dosyası dayanıklılığı, devam kayıtlarının bütünlüğü, kilit sahipliği
ve PROVEN kararının formal ifade ile sınırlandırılması.

## Yapılan değişiklikler

- Var olan runtime.json bozuksa, JSON nesnesi değilse veya durum/ilerleme alanları
  geçersizse RuntimeReadError ile işlem durur. Tamamlanan tur ve sıradaki görev
  varsayılan değerlerle değiştirilmez. Hata ayrı runtime_error.json dosyasına
  kaydedilir; sağlık görünümü PAUSED_ERROR gösterir.
- Tur kimliği, ledger revision/context, zaman ve payload birlikte HMAC ile
  mühürlenir. Kısmi cevaplar anahtar ve fingerprint dahil doğrulanır.
  İmzasız veya değiştirilmiş snapshot/partial kaydı ResearchPaused üretir;
  kayıt silinmez ve prompt'a sokulmaz.
- Eski imzasız step cache kayıtlarını yeniden mühürleyerek güvenilir sayan
  migrasyon kaldırılmıştır. Önceden geçerli imzalanmış step kayıtları,
  SQL status/fingerprint sütunları imzalı içerikle eşleşiyorsa kullanılabilir.
  Geçersiz kayıtlar iş sayacına tamamlanmış olarak katılmaz.
- SQLite bağlantıları commit/rollback sonrası finally ile kapatılır.
- Stale temizleme ve zorla durdurma sonrası güncelleme gerçek OS kilidini alır
  ve güncel durumu yeniden okur. Canlı/yeni worker'ın kilidi ve runtime kaydı
  bu kurtarma yollarıyla silinmez.
- Başarılı Lean sonucu manager kararını kendiliğinden PROVEN'a yükseltmez.
  PROVEN isteyen manager için diğer kanıt kontrollerine ek olarak kayıtlı claim
  ile doğrulanmış tam theorem_type birebir eşleşmelidir. Yalnız dış boşluklar
  yok sayılır; iç boşluklar, yorumlar ve string içeriği dönüştürülmez.
- Bu eşleşme ledger'a PROVEN yazılırken ve eski PROVEN kayıtları okunurken de
  kontrol edilir. Eşleşmeyen eski kayıt diskte değiştirilmeden PROOF_CANDIDATE
  olarak sunulur. Başarılı ama farklı formal ifade formal_candidate altında
  inceleme için korunur.
- LLM Lab kopyasına 10 Eylül'ün sahipsiz kilit, UTF-8 ve yasak Lean axiom
  düzeltmeleri de aktarılmıştır. LLM Lab'a özgü görev kuyruğu, paralel işler,
  retry ve operasyonel claim ekleri korunmuştur.

## Doğrulama

İlk yeni regresyon koşusu düzeltme öncesinde 30 failure, 2 pass üretti.
Düzeltmelerden sonra:
- AILab geniş koşusu: 341 passed, 4 container integration testi kapsam dışı.
- Son eklenen motor seviyesindeki iki bozulma testi dahil odaklı paket:
  her iki çalışma kopyasında 53 passed.
- Yeni kurtarma regresyon dosyası toplam 36 senaryo içerir.
- Ruff: iki çalışma kopyasında temiz.
- Mypy: AILab 35, LLM Lab 57 kaynak dosyasında temiz.
- LLM Lab eski batch UI testi, 3 saniyelik AppTest bekleme süresine takıldı.
  Süre mevcut diğer UI testleriyle uyumlu 25 saniyeye çıkarıldı; ayrı koşu geçti.
  Bu test ayarı bir uygulama hızlandırması olarak sunulmaz.
- LLM Lab geniş koşusu: 737 passed, 2 failed, 1 skipped, 5 deselected.
  İki failure: yukarıdaki UI test zaman aşımı ve container bulunamadığında
  sonuçta başarılı/başarısız koşu sayaçlarının eksik dönmesi. Sayaçlar sıfır
  olarak eklendi; finish-gate testi sahte container fixture'ıyla gerçek finish
  dalını test edecek şekilde ayrıldı. Container yokken ajan/aksiyon çağrılmaması
  için ayrıca regresyon testi eklendi. İlgili deney/arayüz paketi yeniden
  çalıştırıldı: 20 passed. İlk geniş koşu hatasızmış gibi raporlanmamıştır;
  düzeltmelerden sonra geniş paketin tamamı tekrar çalıştırılmamıştır.
- LLM Lab Theorist/kod köprüsü ek regresyon koşusu: 8 passed.

Motor seviyesi testlerde bozuk runtime veya değiştirilmiş snapshot ile devam
girişimi yeni ajan çağrısı yapılmadan durdu. Kontroller geçici test projelerinde
ve sahte model/Lean sonuçlarıyla yapıldı. Gerçek araştırma verileri üzerinde
onarım/migrasyon, ücretli sağlayıcı deneyi veya yeni matematiksel ispat yapılmadı.

## Geçiş ve sınırlar

Eski imzasız snapshot/partial kaydı otomatik olarak güvenilir hale getirilemez.
Bu kayıtla devam durur; eski proje/arşiv korunarak güvenilir girdilerden yeni
çalışma hazırlanmalıdır. Bozuk runtime otomatik tahminle onarılmaz; doğrulanmış
bir kopyadan geri yükleme ayrı işlemdir.

Formal metin eşitliği bir doğal dil çeviri denetçisi değildir. İddia formal Lean
metni olarak kaydedilmelidir; açıklama strategy alanında tutulabilir. Bir
yardımcı lemanın PROVEN olması ana araştırma hedefinin çözüldüğü anlamına gelmez.

HMAC yerel proje anahtarı kullanıyorsa aynı anahtarı ve veriyi değiştirebilen bir
yöneticiye karşı güvenlik sınırı oluşturmaz. Genel ledger tamper-proof garantisi,
tam-state mühürleme ve bilimsel keşif başarısı bu güncellemenin iddiası değildir.
