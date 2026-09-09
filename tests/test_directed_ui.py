"""UI acceptance tests: no network, providers, background processes or live state."""
from __future__ import annotations

from dataclasses import dataclass
import json
import importlib.util
from pathlib import Path
import shutil
import sys
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def directed_ui(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'pages').mkdir()
    (tmp_path / 'examples').mkdir()
    shutil.copy(root / 'pages' / '7_Directed_Task.py', tmp_path / 'pages' / '7_Directed_Task.py')
    shutil.copy(root / 'examples' / 'directed_exact_template.json', tmp_path / 'examples' / 'directed_exact_template.json')
    shutil.copytree(root / 'examples' / 'directed_pilot', tmp_path / 'examples' / 'directed_pilot')
    shutil.copytree(root / 'examples' / 'directed_pilot_zero', tmp_path / 'examples' / 'directed_pilot_zero')
    calls = []
    api = ModuleType('lab.directed')
    api.validate_contract = lambda path: {'ok': True}
    api.preflight_directed_task = lambda path, **kw: {'gate_status': 'FEASIBLE', 'llm_calls_made': 0}

    @dataclass
    class Result:
        task_id: str = 'even-product-demo'
        run_id: str = 'directed-' + 'a' * 32
        status: str = 'QUEUED'
        artifact_root: str = str(tmp_path)

    def run(path, **kwargs):
        calls.append(('run', path, kwargs))
        return Result()

    def resume(project, run, **kwargs):
        calls.append(('resume', project, run, kwargs))
        return Result()

    api.run_directed_task = run
    api.resume_directed_task = resume
    api.get_directed_task_status = lambda *a: {'status': 'RUNNING', 'claim_ledger': {'claims': [{'claim_id': 'TEST', 'status': 'OPEN'}]}}
    api.stop_directed_task = lambda *a: {'status': 'STOP_REQUESTED'}
    api.verify_directed_task = lambda *a: {'ok': True}
    archive = tmp_path / 'COMPLETE_PACKAGE.zip'
    archive.write_bytes(b'fake isolated package')
    api.package_directed_task = lambda *a: {'path': str(archive), 'sha256': 'f' * 64}
    monkeypatch.setitem(sys.modules, 'lab.directed', api)
    app = AppTest.from_file(str(tmp_path / 'pages' / '7_Directed_Task.py'), default_timeout=15).run()
    return app, api, calls, tmp_path


def button(app, label):
    return next(x for x in app.button if x.label == label)


def prepare(app):
    return button(app, 'Sözleşmeyi doğrula ve planı göster').click().run()


def test_directed_page_load_makes_no_execution(directed_ui):
    app, _, calls, root = directed_ui
    assert not app.exception
    assert calls == []
    assert not (root / 'directed_submissions').exists()


def test_directed_limits_are_visible_without_expanding_or_running(directed_ui):
    app, _, calls, _ = directed_ui
    warnings = '\n'.join(x.value for x in app.warning)
    captions = '\n'.join(x.value for x in app.caption)
    assert 'claim ledger tutarlılığını' in warnings
    assert 'matematiksel doğruluk veya bağımsız ispat onayı değildir' in warnings
    assert 'small_b_v1' in warnings and 'genel CAS veya formal ispat sistemi değildir' in warnings
    assert 'tanımları, kod tabanını ve koordinatörü paylaşır' in captions
    assert 'max_total_llm_calls=0' in captions
    assert 'Collatz deposunu seçin' in captions
    assert 'commit edilmemiş çalışma ağacını dondurmaz' in captions
    assert 'bilimsel manifest bağının yerini tutmaz' in captions
    assert not calls and not app.exception


def test_directed_pilot_load_is_free_and_only_edits_draft(directed_ui):
    app, api, calls, root = directed_ui
    api.preflight_directed_task = lambda *a, **kw: pytest.fail('Pilot load must not run preflight')
    button(app, 'Ücretsiz küçük-b pilotunu yükle').click().run()
    assert not app.exception and not calls
    draft = json.loads(app.text_area(key='directed_json').value)
    lanes = draft['agent_plan']['lanes']
    assert len(lanes) == 4
    assert all(lane['execution'] != 'model' for lane in lanes)
    assert app.text_input(key='directed_parent_repo').value == str(root)
    assert app.text_input(key='directed_input_root').value == str(root / 'examples' / 'directed_pilot_zero')
    assert draft['task_id'] == 'small-b-native-pilot-zero-v2'
    for field in ('max_total_llm_calls', 'max_calls_per_lane', 'max_total_tokens', 'max_tokens_per_lane', 'max_total_cost_usd'):
        assert draft['budget'][field] == 0
    assert not (root / 'directed_submissions').exists()


def test_directed_preflight_and_edit_invalidate_launch(directed_ui):
    app, _, calls, _ = directed_ui
    prepare(app)
    assert not app.exception
    assert not button(app, 'Sözleşmeyi dondur ve çalıştır').disabled
    app.text_input(key='directed_input_root').set_value('different').run()
    assert not any(b.label == 'Sözleşmeyi dondur ve çalıştır' for b in app.button)
    assert calls == []


def test_directed_blocked_gate_disables_execution(directed_ui):
    app, api, calls, _ = directed_ui
    api.preflight_directed_task = lambda *a, **kw: {'gate_status': 'BLOCKED'}
    prepare(app)
    assert not app.exception
    assert button(app, 'Sözleşmeyi dondur ve çalıştır').disabled
    assert not calls


def test_directed_validation_failure_has_no_plan(directed_ui):
    app, api, calls, _ = directed_ui
    api.validate_contract = lambda path: {'ok': False, 'error': 'bad frozen claim'}
    prepare(app)
    assert not app.exception
    assert any('bad frozen claim' in e.value for e in app.error)
    assert not any(b.label == 'Sözleşmeyi dondur ve çalıştır' for b in app.button)
    assert not calls


def test_directed_start_once_and_background(directed_ui):
    app, _, calls, _ = directed_ui
    prepare(app)
    button(app, 'Sözleşmeyi dondur ve çalıştır').click().run()
    assert not app.exception
    assert len(calls) == 1 and calls[0][2]['background'] is True
    assert any('RUNNING' in j.value for j in app.json)
    app.run()
    assert len(calls) == 1


def test_directed_lane_apply_rerun_updates_json(directed_ui):
    app, _, _, _ = directed_ui
    button(app, 'Kol ayarlarını sözleşmeye uygula').click().run()
    assert not app.exception
    payload = json.loads(app.text_area(key='directed_json').value)
    assert payload['agent_plan']['lanes'][0]['max_completion_tokens'] == 4096


def test_directed_bad_json_and_unique_submission(directed_ui):
    app, _, calls, root = directed_ui
    prepare(app)
    prepare(app)
    assert len(list((root / 'directed_submissions').glob('*/task.json'))) == 2
    app.text_area(key='directed_json').set_value('{broken').run()
    prepare(app)
    assert app.error and not app.exception and not calls


def test_directed_resume_verify_package(directed_ui):
    app, _, calls, _ = directed_ui
    prepare(app)
    button(app, 'Sözleşmeyi dondur ve çalıştır').click().run()
    button(app, 'Kalan işi sürdür').click().run()
    assert calls[-1][-1]['background'] is True
    button(app, 'Sonuç paketini doğrula').click().run()
    button(app, 'Sonuç paketini hazırla').click().run()
    assert not app.exception
    assert any(x.label == 'Baş araştırmacıya teslim paketini indir' for x in app.get('download_button'))


def load_integration_module(name):
    path = Path(__file__).resolve().parents[1] / 'lab' / (name + '.py')
    spec = importlib.util.spec_from_file_location('isolated_directed_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('resume', [False, True])
def test_worker_directed_dispatch_never_loads_dotenv(tmp_path, monkeypatch, resume):
    from types import SimpleNamespace

    worker = load_integration_module('worker')
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / 'research_state' / 'demo'
    folder.mkdir(parents=True)
    request = {'experiment_method': 'directed_task', 'project_id': 'demo', 'contract_path': 'frozen.json',
               'input_root': 'inputs', 'parent_repo': 'parent', 'resume_run_id': 'run-test' if resume else None}
    (folder / 'worker_request.json').write_text(json.dumps(request))
    monkeypatch.setattr(worker, 'load_dotenv', lambda: pytest.fail('directed task must not load .env'))
    monkeypatch.setattr(worker, 'ProjectManager', lambda: pytest.fail('directed task must not use legacy project metadata'))
    api = ModuleType('lab.directed')
    gate = ModuleType('lab.directed_gate')
    calls = []
    gate.load_contract = lambda path: (SimpleNamespace(project_id='demo'), b'{}')
    api.run_directed_task = lambda *a, **kw: (calls.append(('run', a, kw)) or SimpleNamespace(status='COMPLETED'))
    api.resume_directed_task = lambda *a, **kw: (calls.append(('resume', a, kw)) or SimpleNamespace(status='COMPLETED'))
    monkeypatch.setitem(sys.modules, 'lab.directed', api)
    monkeypatch.setitem(sys.modules, 'lab.directed_gate', gate)
    assert worker.run_project('demo') == 0
    assert calls[0][0] == ('resume' if resume else 'run')
    assert calls[0][2]['background'] is False


def test_directed_worker_request_identity_check(monkeypatch, tmp_path):
    from types import SimpleNamespace

    launcher = load_integration_module('worker_launcher')
    gate = ModuleType('lab.directed_gate')
    gate.load_contract = lambda path: (SimpleNamespace(project_id='demo'), b'{}')
    monkeypatch.setitem(sys.modules, 'lab.directed_gate', gate)
    with pytest.raises(ValueError, match='identity mismatch'):
        launcher.build_directed_request(project_id='wrong', contract_path=tmp_path / 'task.json', input_root=tmp_path, parent_repo=tmp_path)
    request = launcher.build_directed_request(project_id='demo', contract_path=tmp_path / 'task.json', input_root=tmp_path, parent_repo=tmp_path)
    assert request['experiment_method'] == 'directed_task'
    assert 'agents' not in request


def test_exact_example_real_delivery_contains_required_outputs(tmp_path, monkeypatch):
    from lab.directed import run_directed_task, verify_directed_task
    import lab.directed_gate as gate

    example = Path(__file__).resolve().parents[1] / 'examples' / 'directed_exact_template.json'
    payload = json.loads(example.read_text(encoding='utf-8'))
    # Only the external repository identity is synthetic; arithmetic, worker pool,
    # ledger, required-output enforcement and package verification run for real.
    payload['parent_state_commit'] = 'a' * 40
    monkeypatch.setattr(gate, 'parent_commit', lambda path: 'a' * 40)
    request = tmp_path / 'task.json'
    request.write_text(json.dumps(payload), encoding='utf-8')
    result = run_directed_task(request, input_root=tmp_path, parent_repo=tmp_path, root=tmp_path / 'state')
    assert result.status in {'COMPLETED', 'COMPLETED_WITH_OPEN_CLAIMS'}
    package = Path(result.package_path).parent
    for name in payload['required_outputs']:
        assert (package / name).is_file()
    assert verify_directed_task(payload['project_id'], result.run_id, root=tmp_path / 'state')['ok']
