import json

import pytest
from streamlit.testing.v1 import AppTest

from lab.task_builder import build_task, input_manifest


def args():
    return dict(kind='İspat adımını denetle', task_id='test', project_id='math', parent_task_id='principal',
                parent_commit='a'*40, objective='Check this step', statement='A implies B',
                quantifiers='Every integer', definitions=['A and B are defined here'],
                excluded=['Novelty claims'], expected_output='Result, evidence, missing steps', inputs=[],
                model='fixture/model', input_price=1.0, output_price=3.0)


def test_builder_dependency_and_budget():
    data = build_task(**args())
    lanes = data['agent_plan']['lanes']
    assert len(lanes) == 3
    assert lanes[-1]['depends_on'] == ['producer-1', 'producer-2']
    assert 'selected_artifacts' in lanes[-1]['visible_inputs']
    assert data['budget']['max_total_llm_calls'] == 3
    assert data['budget']['max_retries_per_call'] == 0
    assert not data['result_policy']['allow_new_task_creation']


def test_native_scope_and_zero_budget():
    data = build_task(**{**args(), 'kind': 'Sonlu tam sayı kontrolü', 'low': 1, 'high': 20})
    assert data['budget']['max_total_llm_calls'] == 0
    assert data['budget']['max_total_cost_usd'] == 0
    assert data['primary_claim']['statement'] != 'A implies B'
    assert '20' in data['primary_claim']['quantifiers']


def test_explicit_final_allowance_and_validation():
    data = build_task(**{**args(), 'reasoning_cap': 2048, 'final_tokens': 1024})
    assert data['agent_plan']['lanes'][0]['reasoning_effort'] is None
    with pytest.raises(ValueError):
        build_task(**{**args(), 'reasoning_cap': 4000, 'final_tokens': 1024})
    with pytest.raises(ValueError):
        build_task(**{**args(), 'objective': ''})


def test_manifest_exact_bytes_and_paths(tmp_path):
    (tmp_path/'input.txt').write_bytes(b'alpha\r\nbeta')
    import hashlib
    manifest = input_manifest(tmp_path, ['input.txt'])
    assert manifest[0]['sha256'] == hashlib.sha256(b'alpha\r\nbeta').hexdigest()
    for bad in ['../input.txt', '.env', 'secret.txt']:
        with pytest.raises(ValueError):
            input_manifest(tmp_path, [bad])


def test_builder_ui_prepares_without_dispatch(tmp_path, monkeypatch):
    import lab.directed_gate as gate
    import lab.principal_queue as queue
    monkeypatch.setattr(gate, 'parent_commit', lambda _: 'a'*40)
    monkeypatch.setattr(queue, 'run_directed_task', lambda *a, **kw: pytest.fail('No launch'))
    script = 'from pathlib import Path\nfrom lab.task_workbench_ui import render_builder\nrender_builder(Path(' + repr(str(tmp_path)) + '))'
    app = AppTest.from_string(script).run()
    assert not app.exception
    app.selectbox(key='builder_kind').set_value('Sonlu tam sayı kontrolü').run()
    fields = {x.label: x for x in app.text_input}
    for label, value in {'Görev kimliği':'finite-1', 'Proje kimliği':'math',
                         'Baş araştırmacının görev kimliği':'parent',
                         'Baş araştırmacının Git deposu · tam yol':str(tmp_path),
                         'Girdi klasörü · tam yol':str(tmp_path)}.items():
        fields[label].set_value(value)
    next(x for x in app.text_area if x.label == 'Yapılacak iş').set_value('Check finite parity')
    next(x for x in app.button if x.label == 'Taslağı hazırla').click().run()
    assert not app.exception
    snapshot = app.session_state['builder_snapshot']
    assert snapshot['contract']['budget']['max_total_llm_calls'] == 0
    assert not (tmp_path/'research_state/principal_queue').exists()
    next(x for x in app.button if x.label == 'Kuyruğa ekle · çalıştırmaz').click().run()
    assert not app.exception
    q = queue.PrincipalQueue(tmp_path/'research_state/principal_queue', run_root=tmp_path/'research_state')
    assert len(q.list()) == 1 and q.list()[0]['state'] == 'QUEUED'
    next(x for x in app.button if x.label == 'Kuyruğa ekle · çalıştırmaz').click().run()
    assert len(q.list()) == 1
    assert json.loads(next((tmp_path/'research_state/task_drafts').glob('*.json')).read_text(encoding='utf-8')) == snapshot['contract']


def test_review_rejects_unseen_result_version(tmp_path):
    from lab.research_review import review_snapshot, record_review
    folder = tmp_path/'run'
    (folder/'lanes/a').mkdir(parents=True)
    (folder/'TASK_CONTRACT.json').write_text(json.dumps({'agent_plan': {'lanes': [{'lane_id': 'a'}]}}))
    result = folder/'lanes/a/RESULT.json'
    result.write_text(json.dumps({'public_findings': 'Original'}))
    preview = review_snapshot(folder, 'a')
    result.write_text(json.dumps({'public_findings': 'Changed'}))
    with pytest.raises(ValueError, match='since preview'):
        record_review(tmp_path/'reviews.json', folder, 'a', 'useful', 'note', 'Reviewer', 1,
                      expected_bindings=preview['bindings'])
    assert not (tmp_path/'reviews.json').exists()


def test_queue_rejects_contract_changed_since_preview(tmp_path):
    from lab.principal_queue import PrincipalQueue
    payload = build_task(**args())
    path = tmp_path/'task.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    queue = PrincipalQueue(tmp_path/'queue')
    with pytest.raises(ValueError, match='since preview'):
        queue.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key='same', expected_contract_hash='0'*64)
    assert queue.list() == []


def test_native_builder_queue_delivery_end_to_end(tmp_path, monkeypatch):
    import lab.directed_gate as gate
    import lab.principal_queue as queue_module
    from lab.directed import run_directed_task
    from lab.delivery_check import build_delivery_check
    from pathlib import Path
    monkeypatch.setattr(gate, 'parent_commit', lambda _: 'a'*40)
    # Execute the real native engine synchronously in this isolated fixture.
    monkeypatch.setattr(queue_module, 'run_directed_task',
                        lambda path, **kw: run_directed_task(path, **{**kw, 'background': False}))
    payload = build_task(**{**args(), 'kind': 'Sonlu tam sayı kontrolü', 'high': 20})
    path = tmp_path/'contract.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    q = queue_module.PrincipalQueue(tmp_path/'queue', run_root=tmp_path/'state')
    item = q.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key='native')
    finished = q.dispatch(item['job_id'])
    assert finished['state'] == 'DISPATCHED'
    folder = Path(finished['artifact_root'])
    report = build_delivery_check(folder)
    assert report['delivery_status'] == 'COMPLETE'
    assert report['scientific_validation'] == 'NOT_PERFORMED'
    runtime = json.loads((folder/'runtime.json').read_text(encoding='utf-8'))
    assert runtime['usage']['calls'] == 0
    assert q.submit(path, input_root=tmp_path, parent_repo=tmp_path, idempotency_key='native')['job_id'] == item['job_id']
