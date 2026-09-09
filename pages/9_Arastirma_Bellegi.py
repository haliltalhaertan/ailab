"""Human-directed research memory, finite candidate checks and source-bound reviews."""
from pathlib import Path
import json

import streamlit as st

from lab.directed_monitor import discover_runs, load_roots, lane_names
from lab.research_memory import build_memory, check_proposal
from lab.research_candidates import evaluate_candidates
from lab.research_review import record_review, build_reviewed_report

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'research_state' / 'research_reviews.json'
st.set_page_config(page_title='Araştırma belleği', layout='wide')
st.title('Bulgular')
st.caption('Geçmiş iddialar ve baş araştırmacı değerlendirmeleri · Model çağrısı yok.')
try:
    roots = [ROOT / 'research_state', *load_roots(ROOT / 'research_state/monitor_roots.json')]
    runs = discover_runs(roots)
except (OSError, ValueError) as exc:
    st.error(str(exc))
    runs = []
options = {row['folder']: row for row in runs}

view = st.radio('Çalışma alanı', ['Araştırma belleği', 'Baş araştırmacı değerlendirmesi', 'Aday kontrolü'], horizontal=True, key='research_view', label_visibility='collapsed', format_func=lambda v: {'Araştırma belleği': 'Geçmiş iddialar', 'Baş araştırmacı değerlendirmesi': 'Değerlendirme', 'Aday kontrolü': 'Örnek test seti'}[v])
if view == 'Araştırma belleği':
    selected = st.multiselect('Belleğe alınacak çalışmalar', list(options),
                             default=list(options)[:10], format_func=lambda p: options[p]['project_id'] + ' · ' + options[p]['run_id'][-8:])
    st.caption('Aynı iddia ancak tanım alanı ve varsayımları da eşleşiyorsa tekrar sayılır. Metin eşleşmesi literatür yeniliği denetimi değildir. Kaynaklarda bildirilen ispat durumları burada yeniden doğrulanmaz.')
    if st.button('Kaynakları karşılaştır ve belleği oluştur', disabled=not selected):
        try:
            st.session_state['research_memory_snapshot'] = build_memory([Path(p) for p in selected])
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    memory = st.session_state.get('research_memory_snapshot')
    if memory:
        st.caption('Son oluşturulan bellek görüntüsü gösteriliyor. Yeni sonuçları almak için yeniden oluşturun.')
        records = memory.get('records', [])
        summary = st.columns(3)
        summary[0].metric('İddia kaydı', len(records))
        summary[1].metric('Çelişen kayıt', sum(bool(r.get('disagreement')) for r in records))
        summary[2].metric('Kaynak uyarısı', len(memory.get('warnings', [])))
        for warning in memory.get('warnings', []):
            st.warning(str(warning))
        if not records:
            st.info('Seçilen çalışmalarda henüz iddia kaydı yok. Tamamlanmış başka bir çalışma seçebilirsiniz.')
        st.dataframe([{'İddia': r.get('statement', ''), 'Alan': r.get('domain', ''),
                       'Kaynak sayısı': len(r.get('observations', [])),
                       'Bildirilen durumlar': ', '.join(r.get('reported_statuses', [])),
                       'Çelişki': r.get('disagreement', False),
                       'Eksik adım sayısı': len(r.get('missing_steps', []))} for r in records], hide_index=True)
        with st.expander('Teknik kayıtlar ve kaynak hashleri'):
            st.json(memory, expanded=False)
        st.download_button('Kaynaklı belleği indir', json.dumps(memory, ensure_ascii=False, indent=2),
                           'RESEARCH_MEMORY.json', 'application/json')
        with st.form('proposal_lookup'):
            statement = st.text_area('Tekrar kontrolü yapılacak iddia')
            domain = st.text_input('Tanım alanı / niceleyiciler')
            assumptions = st.text_area('Varsayımlar · her satıra bir varsayım')
            lookup = st.form_submit_button('Önceki kayıtlarda ara')
        if lookup:
            try:
                match = check_proposal(memory, statement, domain, [x for x in assumptions.splitlines() if x.strip()])
                if match['match'] == 'EXACT_BINDING_MATCH':
                    st.success('Aynı iddia, tanım alanı ve varsayımlarla önceki kayıtlarda bulundu.')
                else:
                    st.info('Tam eşleşme bulunamadı. Bu sonuç iddianın yeni olduğunu göstermez.')
                with st.expander('Eşleşmenin teknik ayrıntıları'):
                    st.json(match)
            except ValueError as exc:
                st.error(str(exc))

if view == 'Aday kontrolü':
    st.caption('Bu sürüm küçük değerlendirme setindeki dört sonlu kontrolü ve iki insan denetimi görevini destekler. Genel matematiksel ispat veya otomatik yeni aday üretimi yapmaz.')
    upload = st.file_uploader('Aday dosyası · JSON', type=['json'], max_upload_size=2)
    example = ROOT / 'examples/research_candidates.json'
    if example.is_file():
        st.download_button('Aday dosyası örneğini indir', example.read_bytes(), 'candidates.example.json', 'application/json')
    if st.button('Adayları değerlendir', disabled=upload is None):
        try:
            if upload is None or upload.size > 2 * 1024 * 1024:
                raise ValueError('Aday dosyası en fazla 2 MB olmalı.')
            payload = json.loads(upload.getvalue())
            st.session_state['candidate_archive'] = evaluate_candidates(payload)
        except (ValueError, TypeError, OSError) as exc:
            st.error(str(exc))
    archive = st.session_state.get('candidate_archive')
    if archive:
        st.caption('Son değerlendirilen dosyanın sonuçları gösteriliyor. Yeni dosya yüklediyseniz Adayları değerlendir düğmesine basın.')
        status_labels = {'EXACT_PASS': 'Sonlu kontrolden geçti', 'EXACT_FAIL': 'Sonlu kontrolden geçmedi',
                         'FAIL': 'Sonlu kontrolden geçmedi', 'REVIEW_REQUIRED': 'İnsan incelemesi gerekiyor',
                         'MISSING': 'Cevap eksik', 'INVALID': 'Geçersiz cevap'}
        counts = st.columns(3)
        counts[0].metric('Aday', archive.get('candidate_count', 0))
        counts[1].metric('Birebir tekrar', archive.get('duplicate_count', 0))
        counts[2].metric('İnceleme bekleyen', archive.get('review_required_count', 0))
        st.dataframe([{'Aday': row['candidate_id'], 'Görev': row['task_id'],
                       'Sonuç': status_labels.get(row['status'], row['status']),
                       'Önceki aday': row.get('parent_id') or '—',
                       'Tekrarı olduğu aday': row.get('duplicate_of') or '—'}
                      for row in archive.get('candidates', [])], hide_index=True)
        with st.expander('Aday cevapları ve teknik ayrıntılar'):
            st.json(archive, expanded=False)
        st.download_button('Aday kontrol raporunu indir', json.dumps(archive, ensure_ascii=False, indent=2),
                           'CANDIDATE_ARCHIVE.json', 'application/json')

if view == 'Baş araştırmacı değerlendirmesi':
    st.caption('Bu insan değerlendirmesi sonuç dosyasının hash değerine bağlanır. Sonuç değişirse eski değerlendirme güncel sayılmaz. Donmuş deney paketi değiştirilmez; girilen araştırmacı adı kimlik doğrulaması değildir.')
    if not options:
        st.info('İncelenecek çalışma kaydı bulunamadı. Canlı görevler sayfasından kayıt klasörünü ekleyebilirsiniz.')
    else:
        run = st.selectbox('İncelenecek çalışma', list(options), format_func=lambda p: options[p]['project_id'] + ' · ' + options[p]['run_id'][-8:])
        try:
            reviewed = build_reviewed_report(Path(run), STORE)
            names = lane_names(Path(options[run]['root']), Path(run), options[run]['runtime'])
            if names:
                lane = st.selectbox('İncelenecek kol', names)
                selected_result = next((row for row in reviewed.get('lanes', []) if row['lane_id'] == lane), {})
                with st.container(border=True):
                    st.write('**Seçilen işin özeti**')
                    st.text(selected_result.get('what_was_tried') or 'Görev açıklaması kaydedilmemiş.')
                    st.write('**Sonuçtan alıntı**')
                    st.text(selected_result.get('result_excerpt') or 'Henüz nihai cevap kaydedilmemiş.')
                    st.caption('Bu görünüm ilk 1.000 karakteri gösterir. Tam cevabı ve reasoning kaydını Canlı görevler sayfasından inceleyebilirsiniz.')
                    st.write('**Açık kalan adım**')
                    st.text(selected_result.get('first_missing_step') or 'Eksik adım bildirilmemiş; bu, ispatın tamamlandığı anlamına gelmez.')
                    prior = selected_result.get('human_review')
                    if prior:
                        if prior.get('stale'):
                            st.warning('Önceki değerlendirmeden sonra sonuç değişmiş. Önceki karar güncel sayılmıyor.')
                        else:
                            st.caption('Bu kol için daha önce değerlendirme kaydedilmiş. Yeni kayıt önceki kararın yerine güncel değerlendirme olarak kullanılır.')
                        st.text(prior.get('note', ''))
                with st.form('human_review'):
                    reviewer = st.text_input('Değerlendiren araştırmacı')
                    decision = st.selectbox('Karar', ['needs_work', 'useful', 'not_useful'],
                                            format_func=lambda v: {'needs_work':'İnceleme / düzeltme gerekiyor','useful':'Araştırma için yararlı','not_useful':'Yararlı değil'}[v])
                    note = st.text_area('Kararın gerekçesi')
                    minutes = st.number_input('İnceleme süresi · dakika', min_value=0.0, value=0.0, step=1.0)
                    save = st.form_submit_button('Değerlendirmeyi kaydet')
                if save:
                    record_review(STORE, Path(run), lane, decision, note, reviewer, minutes)
                    reviewed = build_reviewed_report(Path(run), STORE)
                    st.success('Değerlendirme kaynak hash değerleriyle kaydedildi.')
            if reviewed.get('model_metrics'):
                st.dataframe(reviewed['model_metrics'], hide_index=True)
            st.download_button('Değerlendirmeli raporu indir', json.dumps(reviewed, ensure_ascii=False, indent=2),
                               'REVIEWED_RESEARCH_REPORT.json', 'application/json')
        except (ValueError, OSError, TimeoutError) as exc:
            st.warning(str(exc))
