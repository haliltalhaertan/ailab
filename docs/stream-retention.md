# Run stream retention

`trace.jsonl`, her tamamlanmış LLM çağrısının tam cevap metnini ve sağlayıcının görünür reasoning metnini `llm_call` event'inde tutar. `stream.jsonl` yalnız canlı, parça-parça görüntüleme ve kesinti sırasında partial çalışma için yüksek hacimli yardımcı kanaldır.

Worker tamamlandığında ham `stream.jsonl` dosyası `stream.jsonl.gz` olarak sıkıştırılır. Arayüz ve Ham Loglar bu arşivi okuyabilir.

30 günden eski tamamlanmış run'larda alan kazanmak için `stream.jsonl.gz` silinebilir; normal geçmiş/audit görünümü için `trace.jsonl` yeterlidir. Aktif veya yarım kalmış bir run'ın stream/partial dosyalarını otomatik silme.
