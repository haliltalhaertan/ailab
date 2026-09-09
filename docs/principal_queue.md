# Baş araştırmacı görev kuyruğu

Kuyruk yerel SQLite dosyasında tutulur. Göndermek model çağrısı veya worker başlatmaz.
Görev sözleşmesinin şeması gönderimde kontrol edilir; tam baytları veritabanına alınır.
Input dosyaları kopyalanmaz: sözleşmedeki hash'ler ve repo commit'i dispatch sırasında
mevcut directed preflight tarafından yeniden doğrulanır. Değişen girdiler engellenir.

```python
from lab.principal_queue import PrincipalQueue
q = PrincipalQueue('research_state/principal_queue', run_root='research_state')
job = q.submit('task.json', input_root='inputs', parent_repo='parent-repository',
               idempotency_key='chief-request-001')
q.list()                       # Yalnız kayıtlı kuyruk; çağrı başlatmaz
q.status(job['job_id'])         # Bağlı worker runtime bilgisini tazeler
q.dispatch(job['job_id'])       # AÇIK başlatma talebi; model maliyeti oluşabilir
```

Aynı anahtar ve tam aynı sözleşme baytları/kök yolları eski job_id'yi döndürür.
Aynı anahtar farklı içerikle gönderilirse reddedilir. Yeni anahtar ayrı iş kabul
edilir; aynı görevi tekrar başlatma yetkisi olarak yalnız bilinçli kullanılmalıdır.

CLI, proje Python ortamından çalıştırılır:

```text
python -m lab.principal_queue --root research_state/principal_queue --run-root research_state submit task.json --input-root inputs --parent-repo parent-repository --idempotency-key chief-request-001
python -m lab.principal_queue --root research_state/principal_queue list
python -m lab.principal_queue --root research_state/principal_queue status JOB_ID
python -m lab.principal_queue --root research_state/principal_queue dispatch JOB_ID
python -m lab.principal_queue --root research_state/principal_queue retry-preflight JOB_ID
```

`state`: QUEUED, DISPATCHING, BLOCKED_PREFLIGHT veya DISPATCHED. Worker durumunu
ayrı `run_status` alanı gösterir. DISPATCHED işin bilimsel olarak başarılı veya
tamamlanmış olduğu anlamına gelmez. `run_id`, `artifact_root`, `contract_hash`,
`payload_hash`, `task_id`, `project_id`, `objective`, zamanlar ve `detail` döner.

Dispatch SQLite yazma transaction'ı ile tek iş üzerinde hak alır. Worker başlatma
sırasında süreç çökerse veya cevap kaybolursa DISPATCHING kalır; otomatik yeniden
çağrı yoktur. Bu kayıt elle run dosyalarıyla uzlaştırılmalıdır. Yalnız kesin olarak
başlatılmamış BLOCKED_PREFLIGHT işi `retry_preflight` ile tekrar kuyruğa alınabilir.
Bu işlem başlatmaz; sonrasında ayrıca dispatch gerekir. Zamanlayıcı/servis/API
sunucusu kurulmaz. Yerel CLI ve Python API baş araştırmacı bağlantısıdır.
