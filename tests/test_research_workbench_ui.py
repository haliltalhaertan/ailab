import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def prepare(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    page = tmp_path / 'pages/9_Arastirma_Bellegi.py'
    page.parent.mkdir()
    page.write_text((repo / 'pages' / page.name).read_text(encoding='utf-8'), encoding='utf-8')
    run = tmp_path / 'research_state/p/directed/r'
    (run / 'lanes/a').mkdir(parents=True)
    (run / 'package').mkdir()
    data = {
        'runtime.json': {'status':'COMPLETED_WITH_OPEN_CLAIMS', 'completed_lanes':['a'],
                         'lanes':{'a':'COMPLETED'}, 'usage':{'lanes':{'a':{'calls':1,'provider_cost_usd':0.01,'provider_cost_complete':True}}}},
        'TASK_CONTRACT.json': {'task_id':'test', 'agent_plan':{'lanes':[{'lane_id':'a','model':'fixture/model','role':'Review','reasoning_effort':'low'}]}},
        'lanes/a/RESULT.json': {'status':'COMPLETED','public_findings':'Bounded result','first_missing_step':'Principal review','usage':{'cost_usd':0.01}},
        'package/CLAIM_LEDGER.json': {'claims':[{'claim_id':'c','statement':'An identity','domain':'integer x','assumptions':[],'status':'OPEN','first_missing_step':'Proof'}]},
    }
    for name, value in data.items():
        (run/name).write_text(json.dumps(value), encoding='utf-8')
    return page, run


def button(app, label):
    return next(x for x in app.button if x.label == label)


def test_memory_build_and_human_review_flow(tmp_path):
    page, run = prepare(tmp_path)
    original = (run/'lanes/a/RESULT.json').read_bytes()
    app = AppTest.from_file(str(page), default_timeout=20).run()
    assert not app.exception
    button(app, 'Kaynakları karşılaştır ve belleği oluştur').click().run()
    assert not app.exception
    assert app.session_state['research_memory_snapshot']['records'][0]['statement'] == 'An identity'
    app.radio(key='research_view').set_value('Baş araştırmacı değerlendirmesi').run()
    next(x for x in app.text_input if x.label == 'Değerlendiren araştırmacı').set_value('Test reviewer')
    next(x for x in app.text_area if x.label == 'Kararın gerekçesi').set_value('Synthetic usefulness decision')
    next(x for x in app.selectbox if x.label == 'Karar').set_value('useful')
    button(app, 'Değerlendirmeyi kaydet').click().run()
    assert not app.exception
    assert (tmp_path/'research_state/research_reviews.json').is_file()
    assert (run/'lanes/a/RESULT.json').read_bytes() == original
    assert any('kaydedildi' in x.value for x in app.success)


def test_empty_installation_keeps_candidate_tool_available(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    page = tmp_path/'pages/9_Arastirma_Bellegi.py'
    page.parent.mkdir()
    page.write_text((repo/'pages'/page.name).read_text(encoding='utf-8'), encoding='utf-8')
    app = AppTest.from_file(str(page), default_timeout=20).run()
    assert not app.exception
    assert button(app, 'Kaynakları karşılaştır ve belleği oluştur').disabled
    app.radio(key='research_view').set_value('Aday kontrolü').run()
    assert button(app, 'Adayları değerlendir').disabled
