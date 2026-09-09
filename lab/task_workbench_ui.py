"""Compact UI over strict contracts, explicit dispatch and read-only delivery checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import streamlit as st

from lab.task_builder import KINDS, build_task, input_manifest


def queue_for(root: Path):
    from lab.principal_queue import PrincipalQueue
    return PrincipalQueue(root / 'research_state/principal_queue', run_root=root / 'research_state')


def render_builder(root: Path) -> None:
    kind = st.selectbox('Görev türü', list(KINDS), key='builder_kind')
    native = kind == 'Sonlu tam sayı kontrolü'
    with st.form('task_builder'):
        ids = st.columns(3)
        task_id = ids[0].text_input('Görev kimliği', placeholder='kontrol-001')
        project_id = ids[1].text_input('Proje kimliği', placeholder='collatz')
        parent_id = ids[2].text_input('Baş araştırmacının görev kimliği', placeholder='CP20-...')
        objective = st.text_area('Yapılacak iş', placeholder='Hangi soruyu, hangi sınırlar içinde inceleyeceğiz?')
        if native:
            predicate = st.text_input('Kontrol edilecek koşul', 'n*(n+1)%2 == 0')
            bounds = st.columns(2)
            low = int(bounds[0].number_input('n başlangıcı', value=1, min_value=1))
            high = int(bounds[1].number_input('n sonu', value=200, min_value=1))
            assumptions = st.text_area('Varsayımlar · satır başına bir koşul')
            statement, quantifiers, definitions = '', '', ''
        else:
            statement = st.text_area('İncelenecek iddia')
            quantifiers = st.text_input('Tanım alanı ve niceleyiciler', placeholder='Her ... için / yalnız ... aralığında')
            definitions = st.text_area('Tanımlar ve varsayımlar · satır başına bir madde')
            low, high, predicate, assumptions = 1, 200, '', ''
        expected = st.text_area('Beklenen teslim', 'Kısa sonuç; kullanılan kanıt veya hesaplama; açık kalan adım ve kapsam sınırları.')
        with st.expander('Girdiler ve araştırma bağı'):
            repo = st.text_input('Baş araştırmacının Git deposu · tam yol')
            inputs_root = st.text_input('Girdi klasörü · tam yol')
            paths = st.text_area('Girdi dosyaları · klasöre göre göreli yollar, satır başına bir dosya')
            excluded = st.text_area('Kapsam dışında kalanlar', 'Yeni görev başlatma\nVarsayımları genişletme\nKanıtsız yenilik iddiası')
            st.caption('Yalnız belirttiğiniz dosyalar hash ile bağlanır. HEAD kimliği, commit edilmemiş dosyaların tamamını dondurmaz.')
        model, producers, auditor, effort = '', 1, False, 'low'
        completion, reasoning_cap, final_tokens = 4096, None, 0
        in_price, out_price, cost_limit = 0.0, 0.0, 0.0
        if not native:
            with st.expander('Model ve bütçe', expanded=True):
                model = st.text_input('Model kimliği', placeholder='sağlayıcı/model — kullanmak istediğiniz kesin kimlik')
                cols = st.columns(3)
                producers = int(cols[0].number_input('Üretici kol', 1, 8, 2))
                effort = cols[1].selectbox('Düşünme düzeyi', ['low', 'none', 'minimal', 'medium', 'high'])
                cost_limit = cols[2].number_input('Toplam maliyet tavanı · USD', min_value=0.001, value=0.5, format='%.3f')
                auditor = st.checkbox('Üreticiler tamamlanınca denetçi çalıştır', value=True)
                prices = st.columns(2)
                in_price = prices[0].number_input('Girdi fiyat tavanı · USD / 1M token', min_value=0.0, value=1.0)
                out_price = prices[1].number_input('Çıktı fiyat tavanı · USD / 1M token', min_value=0.0, value=3.0)
                st.caption('Bunlar sizin belirlediğiniz tavanlardır; canlı model fiyatı değildir. Denetçi de aynı modeli kullanır.')
                completion = int(st.number_input('Kol başına çıktı token tavanı', 256, 65536, 4096))
                use_cap = st.checkbox('Sayısal reasoning sınırı ve cevap payı kullan')
                cap_cols = st.columns(2)
                requested_cap = int(cap_cols[0].number_input('Reasoning token tavanı', 1, 65536, 2048))
                requested_final = int(cap_cols[1].number_input('Nihai cevap token payı', 0, 65536, 1024))
                if use_cap:
                    reasoning_cap, final_tokens = requested_cap, requested_final
                st.caption('Sayısal sınır seçilirse düşünme düzeyinin yerine geçer; desteklenmeyen adaptörde ön kontrol engeller. Yalnız low seçimi cevap payını garanti etmez.')
        wall = int(st.number_input('Toplam süre tavanı · saniye', 10, 86400, 600))
        prepared = st.form_submit_button('Taslağı hazırla', type='primary')
    if prepared:
        st.session_state.pop('builder_snapshot', None)
        try:
            from lab.directed_gate import parent_commit
            if not repo.strip() or not inputs_root.strip():
                raise ValueError('Girdiler bölümündeki Git deposu ve girdi klasörü yollarını doldurun.')
            repo_path, input_path = Path(repo).resolve(strict=True), Path(inputs_root).resolve(strict=True)
            payload = build_task(kind=kind, task_id=task_id, project_id=project_id, parent_task_id=parent_id,
                                 parent_commit=parent_commit(repo_path), objective=objective, statement=statement,
                                 quantifiers=quantifiers, definitions=[x for x in definitions.splitlines() if x.strip()],
                                 excluded=[x for x in excluded.splitlines() if x.strip()], expected_output=expected,
                                 inputs=input_manifest(input_path, [x.strip() for x in paths.splitlines() if x.strip()]),
                                 model=model, producers=producers, auditor=auditor, effort=effort,
                                 completion_tokens=completion, reasoning_cap=reasoning_cap, final_tokens=final_tokens,
                                 input_price=in_price, output_price=out_price, cost_limit=cost_limit,
                                 wall_seconds=wall, low=low, high=high, predicate=predicate,
                                 assumptions=[x for x in assumptions.splitlines() if x.strip()])
            st.session_state.builder_snapshot = {'contract': payload, 'parent_repo': str(repo_path), 'input_root': str(input_path)}
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            st.error(str(exc))
    snapshot = st.session_state.get('builder_snapshot')
    if snapshot:
        payload = snapshot['contract']
        st.success('Taslak hazır; henüz kuyruğa eklenmedi veya çalıştırılmadı.')
        st.caption('Aşağıdaki teslim, son Taslağı hazırla işlemine aittir. Formu değiştirdiyseniz yeniden hazırlayın.')
        st.write(f"**{payload['task_id']}** · {len(payload['agent_plan']['lanes'])} kol · En fazla ${payload['budget']['max_total_cost_usd']:.3f}")
        st.text(payload['objective'])
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        with st.expander('Oluşturulan sözleşme'):
            st.json(payload)
        st.download_button('Sözleşmeyi indir', raw, 'TASK_CONTRACT.json', 'application/json')
        key = st.text_input('Tekrar gönderim anahtarı', value=payload['project_id'] + ':' + payload['task_id'], key='builder_submission_key')
        if st.button('Kuyruğa ekle · çalıştırmaz'):
            try:
                drafts = root / 'research_state/task_drafts'
                drafts.mkdir(parents=True, exist_ok=True)
                path = drafts / (hashlib.sha256(raw.encode()).hexdigest() + '.json')
                if path.is_symlink():
                    raise ValueError('Taslak dosyası sembolik bağlantı olamaz.')
                if not path.exists():
                    with path.open('xb') as stream:
                        stream.write(raw.encode('utf-8'))
                if path.read_bytes() != raw.encode('utf-8'):
                    raise ValueError('Kaydedilmiş taslak görüntülenen sözleşmeden farklı. Kuyruğa eklenmedi.')
                item = queue_for(root).submit(path, input_root=snapshot['input_root'], parent_repo=snapshot['parent_repo'], idempotency_key=key,
                                             expected_contract_hash=hashlib.sha256(raw.encode('utf-8')).hexdigest())
                st.success(f"Kuyruk kaydı: {item['job_id']}. Başlatmak için Çalışmalar → Görev kuyruğu.")
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                st.error(str(exc))


def render_queue(root: Path) -> None:
    with st.expander('Görev kuyruğu'):
        st.caption('Kuyruğa eklemek model çağrısı yapmaz. Başlatma, görev sözleşmesinin bütçesini kullanabilir.')
        try:
            queue = queue_for(root)
            rows = queue.list()
            if not rows:
                st.info('Kuyruk boş. Yeni çalışma ekranından görev hazırlayabilirsiniz.')
                return
            options = {r['job_id']: r for r in rows}
            selected = st.selectbox('Kuyruktaki görev', list(options), format_func=lambda k: f"{options[k]['task_id']} · {options[k]['state']}")
            item = options[selected]
            st.text(item.get('objective', ''))
            st.caption(f"Kayıt: {selected} · Durum: {item['state']}")
            if st.button('Kuyruk durumunu yenile'):
                st.json(queue.status(selected))
            if st.button('Seçili görevi başlat · bütçe kullanabilir', disabled=item['state'] != 'QUEUED'):
                st.json(queue.dispatch(selected))
                st.caption('Başlatılan görev için aşağıdaki Görev listesini yenile düğmesini kullanın.')
            if item['state'] == 'DISPATCHING':
                st.warning('Başlatma sonucu belirsiz. Yinelenen çağrıyı önlemek için otomatik yeniden başlatılmaz.')
            if item['state'] == 'BLOCKED_PREFLIGHT':
                st.warning('Ön kontrol engelledi: ' + str(item.get('detail', {})))
                if st.button('Engeli giderdim · yeniden kuyruğa al'):
                    queue.retry_preflight(selected)
                    st.rerun()
            with st.expander('Kuyruk kaydı'):
                st.json(item)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            st.error(str(exc))


def render_run_controls(folder: Path, runtime: dict) -> None:
    from lab.directed import location, resume_directed_task, stop_directed_task
    with st.expander('Çalışmayı yönet'):
        project_id, run_id = folder.parent.parent.name, folder.name
        run_root = folder.parents[2]
        try:
            if location(project_id, run_id, root=run_root).resolve() != folder.resolve():
                raise ValueError('Çalışma klasörü kimliği uyuşmuyor.')
            active = runtime.get('status') in {'QUEUED', 'RUNNING', 'FINALIZING', 'STOP_REQUESTED'}
            if st.button('Durdurma isteği gönder', disabled=not active, key=f'stop-{folder}'):
                st.json(stop_directed_task(project_id, run_id, root=run_root))
            st.caption('Sürdürme yalnız mevcut kapsam ve bütçe içinde denenir; model çağrısı yapabilir. Bütünlük kontrolü başarısızsa başlatılmaz.')
            resumable = runtime.get('status') in {'PARTIAL', 'STOPPED', 'INTERRUPTED', 'TIMEOUT'}
            if st.button('Kalan işi sürdür · bütçe kullanabilir', disabled=not resumable, key=f'resume-{folder}'):
                result = resume_directed_task(project_id, run_id, root=run_root, background=True)
                st.write(str(result.status))
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))


def render_delivery_and_review(root: Path, folder: Path, lane: str) -> None:
    from lab.delivery_check import build_delivery_check
    from lab.research_review import record_review, review_snapshot
    with st.expander('Teslim kontrolü ve değerlendirme'):
        if st.button('Teslimi kontrol et', key=f'delivery-{folder}'):
            st.session_state['delivery_snapshot'] = (str(folder), build_delivery_check(folder))
        saved = st.session_state.get('delivery_snapshot')
        if saved and saved[0] == str(folder):
            report = saved[1]
            st.write('**Teslim durumu:** ' + str(report.get('delivery_status')))
            st.caption('Dosya ve cevap bütünlüğü kontrolüdür; matematiksel doğruluk veya yararlılık onayı değildir. Son kontrol anını gösterir.')
            st.json(report, expanded=False)
            st.download_button('Teslim kontrol raporunu indir', json.dumps(report, ensure_ascii=False, indent=2), 'DELIVERY_CHECK.json', 'application/json')
        if st.button('Değerlendirmek için sonucu yükle', key=f'preview-{folder}-{lane}'):
            try:
                st.session_state['review_preview'] = (str(folder), lane, review_snapshot(folder, lane))
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        preview = st.session_state.get('review_preview')
        if not preview or preview[:2] != (str(folder), lane):
            st.caption('Karar vermeden önce seçili kolun sabit sonuç görüntüsünü yükleyin.')
            return
        st.text(str(preview[2]['result'].get('public_findings') or 'Nihai bulgu metni yok.'))
        st.caption('Bu sabit görüntü değişirse kayıt reddedilir; sonucu yeniden yükleyin.')
        with st.form(f'review-{folder}-{lane}'):
            st.caption(f'Seçili kol: {lane}. Tam cevabı yukarıdan okuyup değerlendirin.')
            reviewer = st.text_input('Değerlendiren araştırmacı')
            decision = st.selectbox('Karar', ['needs_work', 'useful', 'not_useful'], format_func=lambda x: {'needs_work': 'Düzeltme gerekiyor', 'useful': 'Yararlı', 'not_useful': 'Yararlı değil'}[x])
            note = st.text_area('Kararın gerekçesi')
            minutes = st.number_input('İnceleme süresi · dakika', min_value=0.0, value=0.0)
            save = st.form_submit_button('Değerlendirmeyi kaydet')
        if save:
            try:
                record_review(root / 'research_state/research_reviews.json', folder, lane, decision, note, reviewer, minutes,
                              expected_bindings=preview[2]['bindings'])
                st.success('Değerlendirme sonuç hash’iyle kaydedildi.')
            except (ValueError, OSError, TimeoutError) as exc:
                st.error(str(exc))
