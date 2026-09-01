# research_tools

Bu klasörde yalnızca gözden geçirilmiş, deterministic araştırma scriptleri bulunur.

`ScriptTool` LLM'nin ürettiği keyfi Python kodunu çalıştırmaz; yalnızca bu klasöre önceden eklenmiş `.py` dosyalarını çalıştırır. Alt süreç API anahtarlarını miras almaz.

Bir LLM yeni hesaplama isterse akış şu olmalıdır:

1. İstenen hesabı tanımla.
2. Scripti insan/kod-review ile bu klasöre ekle.
3. Küçük doğrulama testlerini yaz.
4. Sonra agent `tool_request={"tool":"script", ...}` ile scripti çağırabilir.
