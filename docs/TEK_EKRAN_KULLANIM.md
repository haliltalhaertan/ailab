# LLM Lab: tek giriş

LLM_Lab_Baslat.cmd dosyasını çift tıklayın veya proje klasöründe çalıştırın:

```powershell
.venv/Scripts/python.exe -m streamlit run portal.py --server.address=127.0.0.1 --server.port=8501
```

Adres: http://127.0.0.1:8501/

- **Görevler ve canlı izleme:** Görev arayın, çalışan olarak kayıtlı görevleri
  filtreleyin, çalışma kolunu seçip reasoning veya cevabı okuyun.
- **Baş araştırmacıdan görev al:** Mevcut görev sözleşmesi hazırlama ekranı.
- **Bellek ve değerlendirme:** Geçmiş iddiaları karşılaştırın, adayları kontrol
  edin ve sonuçları okuyarak insan değerlendirmesi kaydedin.
- **Paralel deneyler:** Mevcut paralel deney ekranı.
- **Mevcut çalışma alanı:** Klasik laboratuvar, projeler ve araştırma kontrolü.
- **Ayarlar ve kayıtlar:** Önceki model/kod ayarları ve ham kayıtlar.

Eski app.py veya tek sayfa girişleri silinmedi. Ayrı portlarda açılan okuyucular
yerine günlük kullanımda bu tek adres tercih edilebilir. Bu portalın açılış
sayfası kayıtları okur; deney veya model çağrısı başlatmaz. Çalıştırma işlemi
yalnız ilgili eski görev/deney sayfasındaki kullanıcı eylemiyle yapılır.

Kol ilerleme göstergesi sonuçlanan işleri sayar; bilimsel başarı oranı değildir.
Çalışan filtresi runtime durumunu kullanır; eski canlılık kaydında ayrıca uyarı
gösterilir. Okuma sırasında canlı yenilemeyi kapatabilirsiniz.
