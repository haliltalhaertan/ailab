"""User-facing summaries retain uncertainty and show evidence before review."""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def page_with_run(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'pages/9_Arastirma_Bellegi.py'
    page = tmp_path / 'pages/9_Arastirma_Bellegi.py'
    page.parent.mkdir()
    page.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    run = tmp_path / 'research_state/project/directed/run'
    (run / 'lanes/a').mkdir(parents=True)
    for name, value in {
        'TASK_CONTRACT.json': {'agent_plan': {'lanes': [{'lane_id': 'a', 'role': 'Find a counterexample'}]}},
        'runtime.json': {'status': 'PARTIAL', 'lanes': {'a': 'PARTIAL'}},
        'lanes/a/RESULT.json': {'status': 'PARTIAL', 'public_findings': 'Checked integers up to 100.',
                                'first_missing_step': 'Unbounded case remains open.'},
    }.items():
        (run / name).write_text(json.dumps(value), encoding='utf-8')
    return page, run


def test_review_displays_result_and_missing_step_without_writing(tmp_path):
    page, run = page_with_run(tmp_path)
    original = (run / 'lanes/a/RESULT.json').read_bytes()
    app = AppTest.from_file(str(page), default_timeout=20).run()
    assert not app.exception
    app.radio(key='research_view').set_value('Baş araştırmacı değerlendirmesi').run()
    visible = [element.value for element in app.text]
    assert 'Checked integers up to 100.' in visible
    assert 'Unbounded case remains open.' in visible
    assert not (tmp_path / 'research_state/research_reviews.json').exists()
    assert (run / 'lanes/a/RESULT.json').read_bytes() == original


def test_missing_memory_source_is_visible_outside_json(tmp_path):
    page, _ = page_with_run(tmp_path)
    app = AppTest.from_file(str(page), default_timeout=20).run()
    next(item for item in app.button if item.label == 'Kaynakları karşılaştır ve belleği oluştur').click().run()
    assert not app.exception
    assert any('No claim ledger' in item.value for item in app.warning)
    assert any('henüz iddia kaydı yok' in item.value for item in app.info)


def test_candidate_statuses_readable_and_do_not_imply_proof(tmp_path):
    page, _ = page_with_run(tmp_path)
    app = AppTest.from_file(str(page), default_timeout=20)
    app.session_state['candidate_archive'] = {
        'candidate_count': 2, 'duplicate_count': 0, 'review_required_count': 1,
        'candidates': [
            {'candidate_id': 'a', 'task_id': 'finite', 'status': 'EXACT_PASS'},
            {'candidate_id': 'b', 'task_id': 'proof', 'status': 'REVIEW_REQUIRED'},
        ],
    }
    app.run()
    assert not app.exception
    app.radio(key='research_view').set_value('Aday kontrolü').run()
    table = next(item.value for item in app.dataframe if 'Aday' in item.value.columns)
    assert list(table['Sonuç']) == ['Sonlu kontrolden geçti', 'İnsan incelemesi gerekiyor']
    assert any('Genel matematiksel ispat' in item.value for item in app.caption)
