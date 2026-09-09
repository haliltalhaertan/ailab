# Araştırma sistemlerinden uyarlanan yaklaşımlar

Bu değişiklikler üçüncü taraf sistemlerin kurulumu veya kod aktarımı değildir.
Aşağıdaki mimari fikirlerden esinlenerek llm-lab için özgün ve sınırlı araçlar
uygulanmıştır. Hiçbir ücretli araştırma veya model karşılaştırması başlatılmamıştır.

| Kaynak | Alınan fikir | Uygulama | Sınır |
|---|---|---|---|
| Kosmos | Araştırma boyunca yapılandırılmış bilgiyi kaynaklarla biriktirme | research_memory; kaynak hashleri, iddia/alan/varsayım bağları, farklı durumların gösterilmesi | Kosmos dünya modeli değildir; exact metin eşleşmesi semantik yenilik denetimi yapmaz |
| ShinkaEvolve | Adayları ölçülebilir değerlendirmeyle seçme ve soylarını izleme | research_candidates; aday/ebeveyn grafiği, tekrarları tek değerlendirme, exact kontrol | Altı görevlik mevcut değerlendirme kümesiyle sınırlı; otomatik kod evrimi veya aday üretimi yok |
| Agent Laboratory | İnsan yönlendirmesi ve geri bildirimi | research_review; yararlılık kararı, inceleme süresi, model/effort ölçüleri | İnsan görüşü matematiksel ispat veya kimliği doğrulanmış onay değildir |

Kaynaklar:
- https://edisonscientific.com/news/announcing-kosmos
- https://github.com/SakanaAI/ShinkaEvolve
- https://agentlaboratory.github.io/

## Kullanım

Ana uygulamada **Araştırma Belleği** sayfası (pages/9_Arastirma_Bellegi.py):

1. İlgili çalışmaları seçip belleği oluşturun. Kaynaklı JSON indirilebilir.
2. Önerilen iddianın metnini, alanını ve varsayımlarını geçmiş kayıtlarla karşılaştırın.
   Eşleşme olmaması yeni keşif anlamına gelmez. Eşleşen görev otomatik iptal edilmez.
3. Aday kontrolü sekmesindeki örnek JSON'u indirip aynı şemada adayları yükleyin.
   Metinler çalıştırılmaz. Sonlu testten geçen eşsiz adaylar listelenir; tüm geçenler
   eşit puanlıdır. İspat metinleri insan incelemesinde kalır.
4. İnsan değerlendirmesi sekmesinde çalışma/kol seçin, sonucu canlı ekranın sonuç
   sekmesinden okuyun ve gerekçeli yararlılık kararını kaydedin.

Değerlendirmeler research_state/research_reviews.json içinde, dondurulmuş koşudan
ayrı tutulur. Sonuç veya sözleşme baytları değişirse eski değerlendirme STALE olur.
Canlı görevler sayfasındaki teslim raporu da bu değerlendirmeleri ve model/effort
başına yararlılık oranını kullanır. Oranın paydası incelenmiş güncel kayıtlardır.
Ücret belirsizse yararlı sonuç başına kesin maliyet verilmez. İnceleme süreleri
en son güncel değerlendirmeleri toplar; geçmiş bütün insan emeğinin toplamı değildir.

Bellek yalnız dışa aktarılan bir kayıttır. Sonraki görevin izinli girdisine eklemek
için baş araştırmacı bu dosyayı ve hashini yeni sözleşmede açıkça belirtmelidir.
Model bağlamına sessizce bellek eklenmez; bağımsız kolların görünürlük sınırları korunur.

## Doğrulama

Yeni modüller, iki arayüz ve mevcut raporlama/izleme testleri: 69 passed.
Ruff ve mypy: temiz. Gerçek kayıtların salt okunur kontrolü: 6 çalışma,
13 iddia bağı, sıfır okuma uyarısı. Bu sayılar bilimsel başarı ölçümü değildir.
Ayrıntılı modül belgeleri: research_memory.md, research_candidates.md, research_review.md.
