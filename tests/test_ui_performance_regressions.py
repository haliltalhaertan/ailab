"""Exercise navigation and malformed input without providers or real experiments."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from test_directed_ui import directed_ui  # noqa: F401
from test_research_friendly_ui import page_with_run

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('payload', [[], {'agent_plan': None}, {'agent_plan': {'lanes': [None]}}, {'agent_plan': {'lanes': 'bad'}}])
def test_malformed_lane_preview_stays_editable(directed_ui, payload):  # noqa: F811
    app, _, calls, _ = directed_ui
    app.text_area(key='directed_json').set_value(json.dumps(payload)).run()
    assert not app.exception
    assert any('önizleme kullanılamıyor' in item.value for item in app.warning)
    assert not calls


def test_memory_navigation_does_not_build_hidden_reports(tmp_path, monkeypatch):
    import lab.research_review as review
    page, _ = page_with_run(tmp_path)
    calls = []
    original = review.build_reviewed_report
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(review, 'build_reviewed_report', counted)
    app = AppTest.from_file(str(page)).run()
    assert not app.exception and not calls
    app.radio(key='research_view').set_value('Aday kontrolü').run()
    assert not app.exception and not calls
    app.radio(key='research_view').set_value('Baş araştırmacı değerlendirmesi').run()
    assert not app.exception and len(calls) == 1


def test_batch_export_only_on_explicit_request(tmp_path, monkeypatch):
    import lab.batch_lab as batch
    folder = tmp_path / ('batch-' + 'a' * 32)
    folder.mkdir()
    monkeypatch.setattr(batch, 'ROOT', tmp_path)
    monkeypatch.setattr(batch, 'batch_path', lambda _: folder)
    monkeypatch.setattr(batch, 'status', lambda _: {'state': 'COMPLETED', 'completed': 1, 'total': 1, 'jobs': []})
    calls = []
    monkeypatch.setattr(batch, 'export', lambda _: calls.append('export') or b'fixture zip')
    monkeypatch.setattr(batch, 'launch', lambda _: pytest.fail('Navigation must not launch'))
    app = AppTest.from_file(str(ROOT / 'pages/6_Paralel_Deneyler.py')).run()
    app.run()
    assert not app.exception and not calls
    assert any('otomatik yenileme beklemede' in item.value for item in app.caption)
    next(b for b in app.button if b.label == 'Sonuç paketini hazırla').click().run()
    assert calls == ['export']
    app.run()
    assert not app.exception and calls == ['export']


def test_monitor_terminal_run_is_idle_and_reasoning_is_visible(tmp_path):
    page, run = page_with_run(tmp_path)
    monitor = page.parent / '8_Canli_Gorevler.py'
    monitor.write_text((ROOT / 'pages' / monitor.name).read_text(encoding='utf-8'), encoding='utf-8')
    (run / 'lanes/a/PARTIAL.json').write_text(json.dumps({'reasoning': 'Visible provider text'}), encoding='utf-8')
    app = AppTest.from_file(str(monitor)).run()
    assert not app.exception
    assert any('Visible provider text' in item.value for item in app.code)
    assert any('otomatik yenileme beklemede' in item.value for item in app.caption)


def test_batch_remove_one_retains_other_jobs(tmp_path, monkeypatch):
    import lab.batch_lab as batch
    monkeypatch.setattr(batch, 'ROOT', tmp_path)
    app = AppTest.from_file(str(ROOT / 'pages/6_Paralel_Deneyler.py')).run()
    next(b for b in app.button if b.label == 'Görev listesine ekle').click().run()
    next(x for x in app.text_input if x.label == 'Görev kimliği').set_value('second')
    next(b for b in app.button if b.label == 'Görev listesine ekle').click().run()
    next(b for b in app.button if b.label == 'Seçili görevi listeden çıkar').click().run()
    assert not app.exception
    assert [j['id'] for j in app.session_state.batch_jobs] == ['second']
