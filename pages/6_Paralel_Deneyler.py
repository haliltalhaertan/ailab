from __future__ import annotations

import json

import streamlit as st

from lab.batch_lab import ROOT, batch_path, create, export, launch, plan, status

st.set_page_config(page_title='Paralel deneyler', layout='wide')
st.title('Baş araştırmacı için paralel deneyler')
st.caption('Açık görevleri aynı anda çalıştırın; her görevin girdisini, kapsamını ve sonucunu ayrı teslim alın. Model çağrısı yapılmaz.')
st.caption('1. Görevleri ekleyin veya dosyadan alın. 2. Listeyi kontrol edin. 3. Paketi çalıştırın. LLM ile araştırma için Baş araştırmacıdan görev al sayfasını kullanın.')
st.info('Matematik kontrolleri sonlu kapsamda doğrulama sağlar. Python görevleri Docker içinde çalışır; programın çalışması matematiksel ispat değildir.')

if 'batch_jobs' not in st.session_state:
    st.session_state.batch_jobs = []
title = st.text_input('Paket adı', 'Baş araştırmacının görevleri')
workers = st.slider('Aynı anda çalışan yerel hesaplama işi', 1, 4, 2)
st.caption('Bu ekranın 1–4 sınırı yerel hesaplama içindir; LLM çalışma kollarının eşzamanlı çağrı sınırı değildir.')

with st.form('add_math_task'):
    st.subheader('Bir matematik kontrolü ekle')
    ident = st.text_input('Görev kimliği', f'kontrol-{len(st.session_state.batch_jobs) + 1}', key='batch_draft_id')
    predicate = st.text_input('Kontrol edilecek koşul', 'n * (n + 1) % 2 == 0')
    low = st.number_input('n başlangıcı', min_value=1, max_value=1_000_000_000, value=2)
    high = st.number_input('n sonu', min_value=1, max_value=1_000_000_000, value=20001)
    assumptions = st.text_area('Varsayımlar (isteğe bağlı, her satır bir koşul)', placeholder='Örnek: n % 2 == 1')
    st.caption('Örnek: collatz_steps(4*n+1) == collatz_steps(n)+2; varsayım: n % 2 == 1, başlangıç: 3. Büyük aralıklar koşul değiştirilmeden parçalara ayrılır.')
    if st.form_submit_button('Görev listesine ekle'):
        job = {'id': ident, 'kind': 'claim_check', 'claim_spec': {
            'variables': {'n': {'min': int(low), 'max': int(high)}},
            'assumptions': [s.strip() for s in assumptions.splitlines() if s.strip()], 'predicate': predicate}}
        try:
            plan({'jobs': [*st.session_state.batch_jobs, job], 'workers': workers})
            st.session_state.batch_jobs.append(job)
            st.success('Görev eklendi.')
        except (ValueError, TypeError) as exc:
            st.error(str(exc))

with st.expander('Baş araştırmacıdan görev dosyası al'):
    st.caption('JSON paketi claim_check ve python görevleri içerebilir. İçe aktarma mevcut görev listesini değiştirir; çalıştırma ayrıca başlatılır.')
    upload = st.file_uploader('Görev paketi', type=['json'], max_upload_size=1)
    if st.button('Dosyadaki görevleri yükle', disabled=upload is None):
        try:
            if upload is None or upload.size > 1024 * 1024:
                raise ValueError('Dosya en fazla 1 MB olabilir')
            payload = json.loads(upload.getvalue())
            plan(payload)
            st.session_state.batch_jobs = payload['jobs']
            st.success('Görevler yüklendi. Eşzamanlı iş sayısı yukarıdaki ayardan alınır.')
        except (ValueError, TypeError) as exc:
            st.error(str(exc))

request = {'title': title, 'workers': workers, 'jobs': st.session_state.batch_jobs}
st.subheader(f"Görev listesi · {len(request['jobs'])} görev")
if request['jobs']:
    st.dataframe([{'Görev': j['id'], 'Tür': j.get('kind', 'claim_check'),
                   'Koşul': j.get('claim_spec', {}).get('predicate', 'Verilen Python programı')} for j in request['jobs']], hide_index=True)
    remove_id = st.selectbox('Listeden çıkarılacak görev', [j['id'] for j in request['jobs']])
    if st.button('Seçili görevi listeden çıkar'):
        st.session_state.batch_jobs = [j for j in request['jobs'] if j['id'] != remove_id]
        st.rerun()
    if st.button('Görev listesini temizle'):
        st.session_state.batch_jobs = []
        st.rerun()
    try:
        prepared = plan(request)
        st.caption(f"{len(prepared['chunks'])} çalışma parçası · {prepared['candidate_points']:,} aday noktası · aynı anda en fazla {workers} iş")
        st.download_button('Görev paketini indir', json.dumps(request, ensure_ascii=False, indent=2), 'gorevler.json', 'application/json')
        if st.button('Paketi çalıştır', type='primary'):
            folder = create(request)
            launch(folder)
            st.session_state.batch_selected = folder.name
            st.session_state.batch_jobs = []
            st.rerun()
    except (ValueError, TypeError, OSError) as exc:
        st.error(str(exc))


st.button('Çalışma listesini yenile')
batch_folders = sorted(ROOT.glob('batch-*'), key=lambda p: p.stat().st_mtime, reverse=True) if ROOT.exists() else []
batch_active = False
for candidate in batch_folders:
    try:
        batch_active = batch_active or status(candidate)['state'] in {'QUEUED', 'RUNNING'}
    except (OSError, ValueError, KeyError):
        pass
if not batch_active:
    st.caption('Çalışan paket yok; otomatik yenileme beklemede.')


@st.fragment(run_every='3s' if batch_active else None)
def progress():
    st.subheader('Çalışmalar ve sonuçlar')
    folders = sorted(ROOT.glob('batch-*'), key=lambda p: p.stat().st_mtime, reverse=True) if ROOT.exists() else []
    if not folders:
        st.caption('Henüz görev paketi çalıştırılmadı.')
        return
    if batch_active:
        try:
            if not any(status(p)['state'] in {'QUEUED', 'RUNNING'} for p in folders):
                st.rerun()
        except (OSError, ValueError, KeyError):
            pass
    names = [p.name for p in folders]
    labels_by_id: dict[str, str] = {}
    for item in folders:
        try:
            metadata = item / 'request.json'
            if metadata.stat().st_size <= 1024 * 1024:
                title_value = json.loads(metadata.read_text(encoding='utf-8')).get('title', '')
                labels_by_id[item.name] = f'{title_value or "Görev paketi"} · {item.name[-8:]}'
        except (OSError, ValueError, AttributeError):
            pass
    previous_selection = str(st.session_state.get('batch_selected') or '')
    def package_label(value: str) -> str:
        return labels_by_id.get(value, value)
    selected = st.selectbox('Görev paketi seç', names, index=names.index(previous_selection) if previous_selection in names else 0,
                            format_func=package_label)
    if selected is None:
        return
    folder = batch_path(selected)
    try:
        data = status(folder)
    except (ValueError, OSError, KeyError):
        st.warning('Durum kaydı henüz okunamıyor.')
        return
    labels = {'QUEUED': 'Başlatılıyor', 'RUNNING': 'Çalışıyor', 'COMPLETED': 'Çalıştırma tamamlandı', 'CANCELLED': 'Durduruldu', 'ERROR': 'Hata', 'INTERRUPTED': 'Çalıştırıcı durmuş'}
    st.write(labels.get(data['state'], data['state']))
    st.caption(f"Tamamlanan parça: {data.get('completed', 0)} / {data.get('total', '?')} · Model çağrısı: 0")
    if data.get('error'):
        st.error(data['error'])
    rows = []
    result_labels = {'FINITE_PASS': 'İstenen sonlu kapsam geçti', 'EXECUTION_ONLY': 'Program çalıştı; iddia doğrulanmadı', 'COUNTEREXAMPLE': 'Karşı örnek bulundu', 'INCONCLUSIVE': 'Tamamlanmamış / sonuçsuz'}
    for j in data.get('jobs', []):
        rows.append({'Görev': j['id'], 'Sonuç': result_labels[j['state']], 'Kontrol edilen': j['checked_points'], 'Karşı örnek': str(j['witness'] or '')})
    if rows:
        st.dataframe(rows, hide_index=True)
    if data['state'] in {'QUEUED', 'RUNNING'}:
        if st.button('Kalan işleri durdur', key='cancel-' + selected):
            (folder / 'cancel').touch()
            st.info('Yeni parçalar başlatılmayacak. Devam eden sınırlı matematik kontrolü tamamlanır; Python çalışmasına durdurma sinyali iletilir.')
    else:
        if st.button('Sonuç paketini hazırla', key='prepare-' + selected):
            try:
                st.session_state['batch_export'] = (selected, export(folder))
            except (OSError, ValueError) as exc:
                st.error(f'Paket hazırlanamadı: {exc}')
        saved = st.session_state.get('batch_export')
        if saved and saved[0] == selected:
            st.caption('Hazırlama anındaki dosyalar gösteriliyor. Yeni kayıtlar için paketi yeniden hazırlayın.')
            st.download_button('Baş araştırmacı için sonuç paketini indir', saved[1], selected + '.zip', 'application/zip', key='export-' + selected)
    st.caption('Sonuç paketi görevler, kapsamlar, çalıştırıcı parmak izleri ve ham çıktıları içerir. Ana araştırma defterine otomatik aktarılmaz.')


progress()
