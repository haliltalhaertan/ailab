from copy import deepcopy
import json
import os

import pytest

from lab.batch_lab import batch_path, create, execute_chunk, export, plan, read, run, summarize


def request(predicate='n * (n + 1) % 2 == 0', maximum=20001):
    return {'workers': 2, 'jobs': [{'id': 'test', 'claim_spec': {
        'variables': {'n': {'min': 2, 'max': maximum}}, 'assumptions': [], 'predicate': predicate}}]}


def test_partition_exact_coverage_and_frozen_claim():
    p = plan(request())
    values = [n for c in p['chunks'] for n in range(c['scope']['n']['min'], c['scope']['n']['max']+1)]
    assert sorted(values) == list(range(2, 20002))
    assert len(values) == len(set(values))
    assert p['jobs'][0]['claim_spec']['variables']['n']['max'] == 20001


def test_multidimensional_partition_does_not_drop_points():
    r = request(maximum=101)
    r['jobs'][0]['claim_spec']['variables']['k'] = {'min': 0, 'max': 200}
    p = plan(r)
    count = sum((c['scope']['n']['max']-c['scope']['n']['min']+1)*(c['scope']['k']['max']-c['scope']['k']['min']+1) for c in p['chunks'])
    assert count == 20100


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(workers=0), lambda r: r.update(workers=5),
    lambda r: r['jobs'].append(deepcopy(r['jobs'][0])),
    lambda r: r['jobs'][0].update(id='../escape'),
    lambda r: r['jobs'][0]['claim_spec']['variables']['n'].update(max=None),
    lambda r: r['jobs'][0]['claim_spec']['variables']['n'].update(max=3_000_000),
    lambda r: r['jobs'][0].update(scope={'n': {'min': 1, 'max': 10}}),
])
def test_invalid_or_unbounded_work_rejected(mutate):
    r = request()
    mutate(r)
    with pytest.raises(ValueError):
        plan(r)


def test_separate_processes_and_full_scope_receipt(tmp_path):
    folder = create(request(), tmp_path)
    result = run(folder)
    assert result['state'] == 'COMPLETED'
    assert result['jobs'][0]['state'] == 'FINITE_PASS'
    assert result['jobs'][0]['checked_points'] == 20000
    receipts = [read(p) for p in folder.glob('tasks/*/result.json')]
    assert all(r['worker_pid'] != os.getpid() for r in receipts)
    assert len({r['worker_pid'] for r in receipts}) == 2
    assert len(export(folder)) > 0
    before = (folder / 'status.json').read_bytes()
    with pytest.raises(ValueError):
        run(folder)
    assert (folder / 'status.json').read_bytes() == before


def test_missing_and_duplicate_receipts_cannot_close_scope(tmp_path):
    p = plan(request())
    r = execute_chunk(str(tmp_path), p['jobs'][0], p['chunks'][0])
    assert summarize(p, [r])[0]['state'] == 'INCONCLUSIVE'
    bad = deepcopy(r)
    bad['job_hash'] = 'wrong'
    with pytest.raises(ValueError):
        summarize(p, [bad])
    with pytest.raises(ValueError):
        summarize(p, [r, r])


def test_false_claim_witness_is_preserved(tmp_path):
    folder = create(request('n < 10', 20), tmp_path)
    summary = run(folder)['jobs'][0]
    assert summary['state'] == 'COUNTEREXAMPLE'
    assert summary['witness'] == {'n': 10}
    assert not summary['proof']


def test_cancel_before_start_runs_nothing(tmp_path):
    folder = create(request(), tmp_path)
    (folder / 'cancel').touch()
    assert run(folder)['state'] == 'CANCELLED'
    assert not list(folder.glob('tasks/*/result.json'))


def test_frozen_request_tampering_rejected(tmp_path):
    folder = create(request(), tmp_path)
    (folder / 'request.json').write_text(json.dumps(request('n == 0')), encoding='utf-8')
    with pytest.raises(ValueError):
        run(folder)
    assert read(folder / 'status.json')['state'] == 'ERROR'


def test_python_workspaces_isolated_and_never_proof(tmp_path, fake_container_runtime):
    p = plan({'jobs': [{'id': 'one', 'kind': 'python', 'source': 'print(123)'},
                       {'id': 'two', 'kind': 'python', 'source': 'assert False'}]})
    receipts = [execute_chunk(str(tmp_path), j, c) for j, c in zip(p['jobs'], p['chunks'])]
    results = summarize(p, receipts)
    assert results[0]['state'] == 'EXECUTION_ONLY'
    assert results[1]['state'] == 'INCONCLUSIVE'
    assert (tmp_path / 'tasks/0000/workspace/experiment.py').read_text() != (tmp_path / 'tasks/0001/workspace/experiment.py').read_text()


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        batch_path('../outside', tmp_path)


def test_empty_assumption_domain_is_not_pass(tmp_path):
    r = request(maximum=20)
    r['jobs'][0]['claim_spec']['assumptions'] = ['n < 0']
    folder = create(r, tmp_path)
    assert run(folder)['jobs'][0]['state'] == 'INCONCLUSIVE'
