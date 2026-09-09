# Baş araştırmacı için paralel deney laboratuvarı

Arayüzde **Paralel Deneyler** sayfasını açın. Koşulu, varsayımları ve aralığı verip görev listesine ekleyin. Bir pakete farklı görevler eklenebilir. Paket başlatılınca sayfa kapatılsa da ayrı süreçte çalışır. Sonuçlar sayfadan ZIP olarak indirilir.

Bu mod model veya reasoning çağrısı yapmaz. Hazır görevleri çalıştırır; araştırma yönünü baş araştırmacı belirler. Ana Collatz defterini değiştirmez. Her paket `batch_runs/batch-...` altında ayrı saklanır. Aynı anda bir paket, paket içinde en fazla dört iş çalışır. Bu ilk sürüm başka aktif paket varken ikinci paketi bekletmek yerine hata verir; etkin paket bitince yeniden gönderin.

## Baş araştırmacının kullanacağı arayüz

Proje klasöründeki Python ortamıyla:

```powershell
.\.venv\Scripts\python.exe -m lab.batch_lab submit gorevler.json --background
.\.venv\Scripts\python.exe -m lab.batch_lab status batch_runs\batch-KIMLIK
.\.venv\Scripts\python.exe -m lab.batch_lab cancel batch_runs\batch-KIMLIK
```

`submit` yeni paket klasörünü döndürür. `--background` olmadan sonuçlanana kadar bekler. Aynı paketi yeniden çalıştırmaz; tekrar için yeni bir submit yapın. İşlemci kontrollerinde iptal mevcut sınırlı parçaların bitmesini bekler, yeni parça başlatmaz. Python görevlerine iptal kontrolü iletilir.

```json
{
  "title": "Collatz ilişkisi ve kontrol örneği",
  "workers": 2,
  "jobs": [
    {
      "id": "collatz-birlesme",
      "kind": "claim_check",
      "claim_spec": {
        "variables": {"n": {"min": 3, "max": null}},
        "assumptions": ["n % 2 == 1"],
        "predicate": "collatz_steps(4*n+1) == collatz_steps(n)+2"
      },
      "scope": {"n": {"min": 3, "max": 20002}}
    },
    {
      "id": "python-ornek",
      "kind": "python",
      "timeout_s": 30,
      "source": "assert sum(range(101)) == 5050\nprint('AILAB_TEST={\"status\":\"PASS\",\"checked_points\":1}')\n"
    }
  ]
}
```

## Sınırlar ve sonuçların anlamı

- Paket: en fazla 100 görev, 250 parça, 2 milyon aday noktası; 1–4 paralel süreç. Çok boyutlu sonlu aralıklar da desteklenir. İddianın tanım alanı ile istenen tarama alanı ayrıdır; tarama sonlu olmak zorundadır.
- Matematik: mevcut `claim_check` işlevleri ve tam aritmetik. Her parça en fazla 10.000 aday noktası ve denetleyicinin kendi iş bütçesiyle sınırlıdır. İş bütçesi aşılması veya boş kabul edilebilir alan INCONCLUSIVE sonucudur.
- Python: mevcut AST politikası ve Docker/Podman gerektirir. Ağ yok, görev başına ayrı klasör, 256 MB bellek, 1 CPU, en fazla 60 saniye, 1 MB çıktı sınırı. Docker yoksa host üzerinde çalıştırmaya geçilmez.
- FINITE_PASS: bütün planlanan kapsam parçalarının, aynı iddia ve kapsam kimliğiyle başarıyla doğrulandığı anlamına gelir. Sonsuz tanım alanının ispatı değildir.
- COUNTEREXAMPLE: yerleşik denetleyicinin tanık bulduğu anlamına gelir. İnsan dilindeki lemma ile verilen koşulun eşleşmesini baş araştırmacı değerlendirmelidir.
- EXECUTION_ONLY: Python programı çalıştı. Yazdırdığı PASS bağımsız iddia doğrulaması değildir.
- INCONCLUSIVE: eksik, başarısız veya doğrulanamayan iş. Paket COMPLETED olması bütün iddiaların geçtiği anlamına gelmez.
- İstek/plan özetleri, kod parmak izleri, her parçanın girdisi ve sonucu, süreç kimlikleri ve Python çıktıları teslim paketindedir. Parmak izleri yerel bütünlük kontrolüdür; saldırgana karşı imzalı bağımsız sertifika değildir.
- Ani süreç ölümü sonrasında bu sürüm otomatik devam etmez. Kısmi çıktılar korunur; eksik kapsam yeni görev olarak gönderilebilir. Ana araştırmaya aktarma ve yenilik/ispat kararları baş araştırmacıda kalır.
