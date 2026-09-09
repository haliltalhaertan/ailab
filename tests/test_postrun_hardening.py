import json

import pytest

from lab.code_experiment import GuardedExperimentWorkspace, experiment_test_report
from lab.research_state import ResearchState
from lab.status_guard import choose_status
from lab.tools import ToolResult


@pytest.mark.parametrize('payload', [
    '{"status":"FAIL","checked_points":1}',
    '{"status":"INCONCLUSIVE","checked_points":2}',
    '{"status":"PASS","checked_points":0}',
    '{"status":"PASS","checked_points":true}',
    'not json',
])
def test_exit_zero_does_not_hide_failed_or_invalid_test(tmp_path, fake_container_runtime, payload):
    ws = GuardedExperimentWorkspace(tmp_path / 'workspace', timeout_s=5)
    assert ws.write_file('exp.py', 'print(' + repr('AILAB_TEST=' + payload) + ')\n').ok
    result = ws.run_python('exp.py')
    assert result.metadata['returncode'] == 0
    assert not result.ok
    assert result.metadata['termination_reason'].startswith('test_')


def test_failure_cannot_be_overwritten_by_later_pass():
    report = 'AILAB_TEST={"status":"FAIL","checked_points":1}\nAILAB_TEST={"status":"PASS","checked_points":2}'
    assert experiment_test_report(report)['status'] == 'INVALID'


def test_legacy_stdout_remains_execution_only():
    assert experiment_test_report('All tests completed.') == {'status': 'UNREPORTED'}


def test_positive_report_is_recorded_without_proof_promotion(tmp_path, fake_container_runtime):
    ws = GuardedExperimentWorkspace(tmp_path / 'workspace', timeout_s=5)
    report = {'status': 'PASS', 'checked_points': 1}
    assert ws.write_file('exp.py', 'assert 2 + 2 == 4\nprint(' + repr('AILAB_TEST=' + json.dumps(report)) + ')').ok
    result = ws.run_python('exp.py')
    assert result.ok
    assert result.metadata['test_report'] == report
    verdict = choose_status('PROOF_CANDIDATE', tool_result=ToolResult(True, 'code_experiment', output=result.output), verifier={'verdict':'PASS'}, critic={'verdict':'KEEP'})
    assert verdict.granted == 'OPEN'


def test_boolean_test_failure_propagates(tmp_path, fake_container_runtime):
    ws = GuardedExperimentWorkspace(tmp_path / 'workspace', timeout_s=5)
    assert ws.write_file('exp.py', 'def test_identity():\n    return False\nassert test_identity()\n').ok
    assert not ws.run_python('exp.py').ok


@pytest.mark.parametrize('metadata', [{'manager_decision':'KILL'}, {'agenda_status':'RETIRED'}])
def test_retired_verified_claim_preserves_evidence_but_leaves_active_agenda(tmp_path, metadata):
    state = ResearchState(tmp_path / 'project')
    item = state.add_item(kind='conjecture', title='Verified but retired', claim='2 + 2 = 4', status='COMPUTATION_PASS', metadata=metadata)
    context = state.research_context()
    assert item.id in context
    assert 'ACTIVE CANDIDATES:' not in context
    assert next(x for x in state.list_items() if x.id == item.id).status == 'COMPUTATION_PASS'
