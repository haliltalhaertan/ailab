"""Static example acceptance, with no Docker or provider execution."""
from pathlib import Path
import ast
import hashlib
import json

from lab.directed_contract import DirectedTaskContract


EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def load_example(name):
    folder = EXAMPLES / name
    raw = json.loads((folder / 'TASK_CONTRACT.json').read_text(encoding='utf-8'))
    contract = DirectedTaskContract.model_validate(raw)
    assert contract.parent_state_commit != '0' * 40
    for entry in contract.inputs:
        assert hashlib.sha256((folder / entry.path).read_bytes()).hexdigest() == entry.sha256
    return folder, contract


def test_container_example_has_frozen_assertions_and_explicit_report():
    folder, contract = load_example('directed_container')
    lane = contract.agent_plan.lanes[0]
    assert lane.execution == 'python_exact'
    source = (folder / lane.source_input).read_text(encoding='utf-8')
    tree = ast.parse(source)
    assert sum(isinstance(node, ast.Assert) for node in ast.walk(tree)) == 2
    assert 'AILAB_TEST={"status":"PASS","checked_points":200}' in source
    assert 'MASTER_FINDINGS.md' in contract.required_outputs


def test_review_example_has_isolated_producers_and_dependent_auditor():
    _, contract = load_example('directed_review')
    a, b, auditor = contract.agent_plan.lanes
    assert not a.depends_on and not b.depends_on
    assert not a.can_read_other_lane_outputs and not b.can_read_other_lane_outputs
    assert a.independence_group != b.independence_group
    assert set(auditor.depends_on) == {a.lane_id, b.lane_id}
    assert auditor.can_read_other_lane_outputs
    assert 'selected_artifacts' in auditor.visible_inputs
    assert 'producer_hidden_reasoning' in auditor.hidden_inputs
    assert all(lane.model == 'provider/model-id' for lane in (a, b, auditor))
