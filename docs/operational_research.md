# Düşük maliyetli araştırma ve iddia doğrulama

Model önerir; denetleyici, sabitlenmiş ifadeyi hesaplar. Başarılı program çalışması,
doğru operasyonel iddia, matematiksel ispat ve bilimsel yenilik ayrı sonuçlardır.
Bu sürüm yeni keşif performansını kanıtlamaz; yanlış iddia–deney eşleşmesini
önlemek ve basit kontroller için model çağrısı harcamamak amacıyla geliştirilmiştir.

## Araştırma akışı

1. Küçük bir hipotez öner. Bilinen karşılaştırma sonucunu ve beklenen farkı yaz.
2. Desteklenen hipotezlerde `claim_spec` ile değişkenleri, varsayımları ve tam
   karşılaştırmayı belirt. Bu yapı operasyonel iddianın tanımıdır. Doğal dildeki
   bilimsel hedefle aynı anlama geldiği ayrıca incelenmelidir.
3. Küçük bir kapsamda `claim_check` kullan. Model tarafından yazılmış denetleyici
   çalıştırılmaz; kesin kesir aritmetiği ve kod deposundaki sabit işlevler kullanılır.
4. Karşıörnek varsa aynı özgün ifadede tekrar değerlendir. Tanımı değiştireceksen
   yeni hipotez oluştur; eski hipotezi bu değişiklikle çürütülmüş sayma.
5. Başarılı sınırlı kontrolün ardından bağımsız veri/kapsam ve literatür incelemesine
   geç. `COMPUTATION_PASS`, ispat veya yenilik belgesi değildir.

## Modelin kullanacağı biçim

```json
{
  "title": "Ardışık çarpımın çiftliği",
  "claim": "n >= 2 için n(n+1) çifttir; bilinen sonuç, denetim örneği.",
  "claim_spec": {
    "variables": {"n": {"min": 2, "max": null}},
    "assumptions": [],
    "predicate": "n*(n+1)%2 == 0"
  },
  "recheck_item_id": "",
  "tool_request": {
    "tool": "claim_check",
    "scope": {"n": {"min": 2, "max": 100}}
  }
}
```

Önceki bir operasyonel iddiayı kontrol etmek için `recheck_item_id` kullanılır.
İfade, kapsam ve varsayımlar aynı kalmalıdır. Eski, yalnız doğal dille yazılmış
kayıtlar otomatik dönüştürülmez. Yeni bir operasyonel hipotez oluşturulup çeviri
incelenmelidir; eski sonuç dosyaları değiştirilmez.

## Desteklenen kapsam ve sınırlar

- En fazla dört tam sayı değişkeni, sekiz varsayım, 10.000 noktalık bir test alanı.
- Tam kesir aritmetiği: `+ - * / // % **`; karşılaştırmalar ve `and/or/not`.
- Sabit işlevler: `abs`, `min`, `max`, `gcd`, `ceil_log2`, `collatz_steps`,
  `residue(n,k)`, `collatz_prefix_lower(a,k)`.
- `collatz_steps` standart `n/2` ve `3n+1` adımlarını sayar; 10.000 adımda
  çözümlenmeyen yörünge yanlış veya sonsuz ilan edilmez.
- `residue` sıfır kalanı `2**k` temsilcisiyle gösterir. `collatz_prefix_lower`,
  temsilcinin 1'e ilk inişinden önceki, en fazla k adımlık önek uzunluğudur.
- Genel kaynak kodu, dosya/ağ erişimi, nesne özellikleri, kayan nokta sabitleri ve
  modelce eklenen işlevler bu denetleyiciye kabul edilmez.
- Sınır aşımı, sıfıra bölme, kapsam dışı örnek ve boş geçerli örnek kümesi
  `INCONCLUSIVE` üretir. Başarılı sonlu test `PROVEN` üretmez.
- Modelin seçtiği bir operasyonel ifadenin denetimi daha geniş araştırma hedefini
  otomatik kapatamaz. Aynı hedef ID'sini yazmak bilimsel eşdeğerlik sağlamaz.

Diğer bilimsel alanlarda serbest `code_experiment` keşif amaçlı veri ve aday üretir.
Programın dönmesi iddiayı doğrulamaz. Alanın ölçümleri, birimleri, hata payları ve
bağımsız veri protokolü için incelenmiş `script`/problem-pack denetleyicisi gerekir.
Bu sürüm istatistiksel anlamlılık, yenilik ya da bütün bilimler için evrensel bir
doğrulayıcı sağlamaz.

## Maliyet ve denetim

`claim_check` LLM ve kodlama ajanı çağırmadan çalışır. Yerel kaynak/specifikasyon
hatalarında verifier, critic ve manager çağrıları atlanır; düzeltme görevi koddan
üretilir. Aynı hata ikinci ayrı adayda tekrarlanırsa çalışma duraklatılır. Bir
modelin farklı rollerle konuşması bağımsız doğrulama sayılmaz.

Kontrol noktalarında tam denetim metni saklanır. Ajanlara verilen bağlamda
operasyonel ifade ve makine kontrolünün kapsamı korunur; kısa başlık bilimsel
ifadenin yerine kullanılmaz.

## Ücretsiz tekrar edilebilir kontrol

```text
python experiments/operational_claim_benchmark.py
pytest tests/test_operational_claims.py
```

Karşılaştırma deneyi gerçek Collatz anlam kaymasını, yanlış karşıörneği, kapsam
dışı girdiyi ve aritmetik kontrol örneklerini kapsar. API çağrısı yapmaz; ucuz bir
modelin yeni buluş üretme oranını ölçmez. Böyle bir performans ölçümü için ayrıca
görülmemiş problemler, sabit çağrı/token bütçesi ve bağımsız sonuç değerlendirmesi
gereklidir.
