from pathlib import Path

from streamlit.testing.v1 import AppTest

from lab.directed_monitor import filter_runs, status_label


def test_filters_active_first_and_plain_text_search():
    rows = [
        {'folder':'a','project_id':'Collatz','run_id':'one','modified':10,'runtime':{'status':'STOPPED'}},
        {'folder':'b','project_id':'Math','run_id':'two','modified':1,'runtime':{'status':'RUNNING'}},
    ]
    assert [r['folder'] for r in filter_runs(rows)] == ['b', 'a']
    assert [r['folder'] for r in filter_runs(rows, 'COLLATZ')] == ['a']
    assert [r['folder'] for r in filter_runs(rows, active_only=True)] == ['b']
    assert filter_runs(rows, '[]') == []
    assert status_label('TIMEOUT') == 'Süre doldu'


def test_portal_opens_monitor_memory_and_preserves_classic(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path/'pages').mkdir()
    (tmp_path/'portal.py').write_text((root/'portal.py').read_text(encoding='utf-8'),encoding='utf-8')
    for source in (root/'pages').glob('*.py'):
        if source.name in {'8_Canli_Gorevler.py','9_Arastirma_Bellegi.py','10_Yeni_Calisma.py'}:
            text = source.read_text(encoding='utf-8')
        else:
            text = 'import streamlit as st\nst.title("Existing page retained")'
        (tmp_path/'pages'/source.name).write_text(text,encoding='utf-8')
    (tmp_path/'app.py').write_text('import streamlit as st\nst.title("Classic preserved")',encoding='utf-8')
    app = AppTest.from_file(str(tmp_path/'portal.py'),default_timeout=25).run()
    assert not app.exception
    assert any(x.value == 'Çalışmalar' for x in app.title)
    hub_source = (tmp_path/'portal.py').read_text(encoding='utf-8').replace(', default=True', '')
    hub_source = hub_source.replace("title='Yeni çalışma',", "title='Yeni çalışma', default=True,")
    (tmp_path/'hub_test.py').write_text(hub_source, encoding='utf-8')
    hub = AppTest.from_file(str(tmp_path/'hub_test.py'), default_timeout=25).run()
    assert not hub.exception
    assert any(x.value == 'Yeni çalışma' for x in hub.title)
    assert len(hub.main.get('page_link')) == 3
    app.switch_page('pages/9_Arastirma_Bellegi.py').run()
    assert not app.exception
    app.switch_page('app.py').run()
    assert not app.exception
    assert any(x.value == 'Classic preserved' for x in app.title)
