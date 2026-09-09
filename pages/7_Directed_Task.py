"""Principal-researcher submissions; editing never mutates a frozen run."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st

from lab.directed import (
    get_directed_task_status, package_directed_task, preflight_directed_task,
    resume_directed_task, run_directed_task, stop_directed_task,
    validate_contract, verify_directed_task,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 1024 * 1024


def plain(value):
    return asdict(value) if is_dataclass(value) and not isinstance(value, type) else value


def submission(text):
    """Never trust a supplied filename and never overwrite a previous submission."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError('Sözleşme bir JSON nesnesi olmalı.')
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    if len(encoded) > MAX_UPLOAD:
        raise ValueError('Sözleşme en fazla 1 MB olabilir.')
    folder = ROOT / 'directed_submissions' / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / 'task.json'
    path.write_bytes(encoded)
    return path


st.set_page_config(page_title='Baş araştırmacı görevi', layout='wide')
st.title('Baş araştırmacı görevi')
st.caption('Tanımları, kapsamı ve bütçesi sabit bir işi paralel çalışma kollarına dağıtın; sonuç paketini baş araştırmacıya teslim edin.')
st.info('Plan kontrolü model çağrısı yapmaz. Çalıştırma, sözleşmedeki model kolları için ücretli çağrı yapabilir. Matematiksel durum ile işlemin tamamlanması ayrı gösterilir.')
with st.expander('Kapsam ve doğrulama sınırları'):
    st.warning(
        'Paket doğrulaması dosya baytlarının bütünlüğünü ve claim ledger tutarlılığını kontrol eder; '
        'matematiksel doğruluk veya bağımsız ispat onayı değildir. '
        'Native sembolik araç yalnız small_b_v1 pilotunu destekler; genel CAS veya formal ispat sistemi değildir.'
    )
    st.caption(
        'Pilotun iki algoritması tanımları, kod tabanını ve koordinatörü paylaşır. '
        'Farklı algoritmalarla çapraz kontrol yapılması, iki bağımsız matematiksel ispat anlamına gelmez.'
    )
    st.caption(
        'Native görevlerde model çağrısı tavanını 0 tutabilirsiniz: max_total_llm_calls=0 hiçbir model çağrısına izin vermez. '
        'Sıfır bütçe model kolu için kullanılamaz; native hesaplamanın süre ve paralellik sınırları ayrıca uygulanır.'
    )

if 'directed_json' not in st.session_state:
    st.session_state.directed_json = (ROOT / 'examples' / 'directed_exact_template.json').read_text(encoding='utf-8')
if 'directed_pending_json' in st.session_state:
    st.session_state.directed_json = st.session_state.pop('directed_pending_json')
    st.session_state.pop('directed_preflight', None)

if st.button('Ücretsiz küçük-b pilotunu yükle'):
    try:
        pilot_root = ROOT / 'examples' / 'directed_pilot_zero'
        st.session_state.directed_json = (pilot_root / 'TASK_CONTRACT.json').read_text(encoding='utf-8')
        st.session_state.directed_parent_repo = str(ROOT)
        st.session_state.directed_input_root = str(pilot_root)
        st.session_state.pop('directed_preflight', None)
        st.success('Pilot sözleşmesi yüklendi. Henüz kontrol veya deney başlatılmadı.')
    except (OSError, UnicodeError) as exc:
        st.error(str(exc))

upload = st.file_uploader('Baş araştırmacının JSON sözleşmesi', type=['json'], max_upload_size=1)
if st.button('Sözleşmeyi düzenleyiciye yükle', disabled=upload is None):
    try:
        if upload is None or upload.size > MAX_UPLOAD:
            raise ValueError('Dosya en fazla 1 MB olabilir.')
        text = upload.getvalue().decode('utf-8-sig')
        if not isinstance(json.loads(text), dict):
            raise ValueError('JSON nesnesi bekleniyor.')
        st.session_state.directed_json = text
        st.session_state.pop('directed_preflight', None)
    except (ValueError, UnicodeError) as exc:
        st.error(str(exc))

with st.expander('Görev sözleşmesini düzenle'):
    st.text_area('Görev sözleşmesi · tüm alanlar düzenlenebilir', key='directed_json', height=250)
parent_repo = st.text_input('Baş araştırmacının deposu (tam klasör yolu)', key='directed_parent_repo')
input_root = st.text_input('İzin verilen girdilerin klasörü (tam yol)', key='directed_input_root')
st.caption(
    'Gerçek Collatz görevi için baş araştırmacının Collatz deposunu seçin. Pilot düğmesi yalnız altyapı kabulü için llm-lab deposunu doldurur. '
    'parent_state_commit / HEAD, commit edilmemiş çalışma ağacını dondurmaz ve bilimsel manifest bağının yerini tutmaz; '
    'araştırma girdilerini ve manifestlerini açık yolları ve SHA256 değerleriyle sözleşmeye ekleyin.'
)
st.caption('Örnekteki sıfırlardan oluşan parent_state_commit alanını gerçek 40 karakterlik commit ile değiştirin. Model kolunda model, reasoning_effort ve fiyat tavanlarını açıkça belirtin.')

try:
    draft = json.loads(st.session_state.directed_json)
    if not isinstance(draft, dict) or not isinstance(draft.get('agent_plan'), dict):
        raise ValueError('agent_plan bir JSON nesnesi olmalı.')
    lanes = draft['agent_plan'].get('lanes')
    if not isinstance(lanes, list) or not all(isinstance(lane, dict) for lane in lanes):
        raise ValueError('agent_plan.lanes bir çalışma kolu nesneleri listesi olmalı.')
    with st.expander('Çalışma kollarının model ve düşünme ayarları'):
        rows = [{k: lane.get(k, 4096 if k == 'max_completion_tokens' else '' if k in {'model', 'reasoning_effort'} else None) for k in ('lane_id', 'role', 'execution', 'model', 'reasoning_effort', 'max_completion_tokens', 'input_price_per_million', 'output_price_per_million')}
                for lane in draft['agent_plan']['lanes']]
        lane_key = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
        edited = st.data_editor(
            rows, disabled=['lane_id', 'role', 'execution'], hide_index=True,
            key='directed_lanes_' + lane_key,
            column_config={
                'model': st.column_config.TextColumn('Model'),
                'reasoning_effort': st.column_config.SelectboxColumn('Düşünme düzeyi', options=['none', 'minimal', 'low', 'medium', 'high']),
                'max_completion_tokens': st.column_config.NumberColumn('En fazla çıktı token', min_value=1, max_value=65536, step=1),
                'input_price_per_million': st.column_config.NumberColumn('Girdi fiyat tavanı / 1M token', min_value=0.0),
                'output_price_per_million': st.column_config.NumberColumn('Çıktı fiyat tavanı / 1M token', min_value=0.0),
            },
        )
        if st.button('Kol ayarlarını sözleşmeye uygula'):
            for lane, row in zip(draft['agent_plan']['lanes'], edited, strict=True):
                for key in ('model', 'reasoning_effort', 'max_completion_tokens', 'input_price_per_million', 'output_price_per_million'):
                    if row.get(key) not in (None, ''):
                        lane[key] = row[key]
                    else:
                        lane.pop(key, None)
            st.session_state.directed_pending_json = json.dumps(draft, ensure_ascii=False, indent=2)
            st.rerun()
except (ValueError, TypeError, KeyError) as exc:
    st.warning(f'Sözleşme düzenlenirken önizleme kullanılamıyor: {exc}')

fingerprint = hashlib.sha256((st.session_state.directed_json + '\n' + parent_repo + '\n' + input_root).encode()).hexdigest()
if st.button('Sözleşmeyi doğrula ve planı göster'):
    try:
        path = submission(st.session_state.directed_json)
        validation = validate_contract(path)
        if not validation.get('ok'):
            raise ValueError(validation.get('error', 'Sözleşme geçersiz.'))
        report = preflight_directed_task(path, input_root=input_root or None, parent_repo=parent_repo or None)
        st.session_state.directed_preflight = {'fingerprint': fingerprint, 'path': str(path), 'report': report}
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        st.error(str(exc))

prepared = st.session_state.get('directed_preflight')
if prepared and prepared['fingerprint'] == fingerprint:
    st.subheader('Dondurulacak plan')
    st.json(prepared['report'])
    st.dataframe([{'Kol': x['lane_id'], 'Rol': x['role'], 'Araç': x.get('execution', 'model'),
                   'Beklediği kollar': ', '.join(x.get('depends_on', [])), 'Bağımsızlık grubu': x.get('independence_group', 'shared')}
                  for x in draft['agent_plan']['lanes']], hide_index=True)
    st.download_button('Sözleşmeyi indir', st.session_state.directed_json, 'task.json', 'application/json')
    feasible = prepared['report'].get('gate_status') in {'FEASIBLE', 'FEASIBLE_WITH_LIMITATIONS'}
    if not feasible:
        st.warning('Ön kontroldeki engeller çözülmeden çalışma başlatılamaz.')
    if st.button('Sözleşmeyi dondur ve çalıştır', type='primary', disabled=not feasible):
        try:
            result = plain(run_directed_task(prepared['path'], input_root=input_root or None, parent_repo=parent_repo or None, background=True))
            result['project_id'] = draft['project_id']
            st.session_state.directed_result = result
            if result.get('run_id'):
                st.success('Çalıştırma isteği gönderildi. Güncel durumu aşağıdan izleyin.')
            else:
                st.warning('Çalışma başlatılamadı; ön kontrol sonucunu inceleyin.')
            st.json(result)
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            st.error(str(exc))

st.subheader('Çalışma ve teslim')
st.caption('Doğrulama PASS sonucu bayt bütünlüğü ve ledger tutarlılığı içindir; matematiksel doğruluk kararı baş araştırmacının incelemesine bağlıdır.')
last = st.session_state.get('directed_result', {})
project = st.text_input('İzlenecek proje kimliği', value=last.get('project_id', ''))
run = st.text_input('İzlenecek çalışma kimliği', value=last.get('run_id', ''))

if project and run:
    st.button('Çalışma durumunu yenile')
    try:
        initial_state = get_directed_task_status(project, run)
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))
        st.stop()
    active_states = {'QUEUED', 'RUNNING', 'FINALIZING', 'STOP_REQUESTED'}
    refreshing = initial_state.get('status') in active_states
    if not refreshing:
        st.caption('Çalışma aktif değil; otomatik yenileme beklemede.')
    @st.fragment(run_every='5s' if refreshing else None)
    def progress():
        try:
            state = get_directed_task_status(project, run)
            if refreshing and state.get('status') not in active_states:
                st.rerun()
            st.json(state)
            ledger = state.get('claims', state.get('claim_ledger'))
            if isinstance(ledger, dict):
                ledger = ledger.get('claims')
            if isinstance(ledger, list) and ledger:
                st.dataframe(
                    [{key: claim.get(key, '') for key in ('claim_id', 'statement', 'status')} for claim in ledger],
                    hide_index=True,
                )
                with st.expander('İddiaların destek kayıtları ve kapsamı'):
                    st.json(ledger)
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    progress()
    try:
        if st.button('Durdurma isteği gönder'):
            st.json(stop_directed_task(project, run))
        if st.button('Kalan işi sürdür'):
            st.json(plain(resume_directed_task(project, run, background=True)))
        if st.button('Sonuç paketini doğrula'):
            st.json(verify_directed_task(project, run))
        if st.button('Sonuç paketini hazırla'):
            packed = package_directed_task(project, run)
            st.session_state.directed_package = {'project': project, 'run': run, 'result': packed}
        stored = st.session_state.get('directed_package', {})
        if stored.get('project') == project and stored.get('run') == run:
            packed = stored['result']
            st.json(packed)
            path = packed.get('path', packed.get('package_path'))
            if path and Path(path).is_file():
                st.download_button('Baş araştırmacıya teslim paketini indir', Path(path).read_bytes(), 'COMPLETE_PACKAGE.zip', 'application/zip')
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        st.error(str(exc))
