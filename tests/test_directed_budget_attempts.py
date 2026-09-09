from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from lab.directed_budget import BudgetExhausted, DirectedBudget
from lab.directed_contract import Budget, Lane


def setup_budget(persist=None):
    limits = Budget(max_total_llm_calls=8, max_calls_per_lane=4, max_retries_per_call=3,
                    max_total_tokens=1000000, max_tokens_per_lane=1000000, max_total_cost_usd=10,
                    max_wall_seconds=60, max_parallel_workers=4)
    lane = Lane(lane_id='first', role='Theorist', model='fake/model', max_calls=4,
                input_price_per_million=1, output_price_per_million=1, max_completion_tokens=128)
    return DirectedBudget(limits, persist=persist), lane


def test_timeout_after_reserve_has_unknown_cost_before_dispatch():
    saved = []
    budget, lane = setup_budget(saved.append)
    attempt = budget.reserve(lane, 'question')
    state = saved[-1]
    assert not state['provider_cost_complete']
    assert state['provider_cost_lower_bound_usd'] == 0
    assert state['provider_cost_upper_bound_usd'] == state['reserved_cost_usd']
    assert state['lanes'][lane.lane_id]['attempts'][attempt]['recorded'] is False


def test_concurrent_completion_cannot_hide_other_timeout():
    budget, lane = setup_budget()
    other = lane.model_copy(update={'lane_id': 'second'})
    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(lambda item: budget.reserve(item, 'q'), [lane, other]))
    budget.record(lane.lane_id, {'cost_usd': .000001}, first)
    state = budget.snapshot()
    assert not state['provider_cost_complete']
    assert state['lanes'][lane.lane_id]['provider_cost_complete']
    assert state['provider_cost_upper_bound_usd'] == pytest.approx(.000001 + state['lanes']['second']['reserved_cost_usd'])
    budget.record('second', {'cost_usd': 0}, second)
    assert budget.snapshot()['provider_cost_complete']


def test_retry_preserves_unresolved_first_attempt_and_reservations():
    budget, lane = setup_budget()
    first = budget.reserve(lane, 'q')
    budget.record(lane.lane_id, {}, first)
    second = budget.reserve(lane, 'q')
    reserved = budget.snapshot()['reserved_cost_usd']
    budget.record(lane.lane_id, {'cost_usd': 0}, second)
    state = budget.snapshot()
    assert not state['provider_cost_complete']
    assert state['reserved_cost_usd'] == reserved
    assert state['unresolved_reserved_cost_usd'] == pytest.approx(reserved / 2)


def test_explicit_replay_is_idempotent_and_conflicting_replay_rejected():
    budget, lane = setup_budget()
    attempt = budget.reserve(lane, 'q')
    usage = {'cost_usd': .000001, 'prompt_tokens': 3, 'completion_tokens': 2}
    budget.record(lane.lane_id, usage, attempt)
    budget.record(lane.lane_id, deepcopy(usage), attempt)
    assert budget.snapshot()['prompt_tokens'] == 3
    assert budget.snapshot()['provider_cost_usd'] == .000001
    with pytest.raises(ValueError, match='Conflicting'):
        budget.record(lane.lane_id, {'cost_usd': 0}, attempt)


@pytest.mark.parametrize('cost', [float('nan'), float('inf'), -1, '0', True])
def test_invalid_cost_does_not_resolve_charge(cost):
    budget, lane = setup_budget()
    attempt = budget.reserve(lane, 'q')
    with pytest.raises(BudgetExhausted, match='Invalid'):
        budget.record(lane.lane_id, {'cost_usd': cost}, attempt)
    assert not budget.snapshot()['provider_cost_complete']


def test_native_zero_calls_complete_and_resume_pending_stays_unknown():
    budget, lane = setup_budget()
    assert budget.snapshot()['provider_cost_complete']
    budget.reserve(lane, 'q')
    restored = DirectedBudget(budget.limits, budget.snapshot())
    assert not restored.snapshot()['provider_cost_complete']
    restored.record(lane.lane_id, {'cost_usd': 0})
    assert restored.snapshot()['provider_cost_complete']


def test_legacy_journal_does_not_trust_old_complete_flag():
    budget, lane = setup_budget()
    budget.reserve(lane, 'q')
    old = budget.snapshot()
    old['lanes'][lane.lane_id].pop('attempts')
    old['provider_cost_complete'] = True
    restored = DirectedBudget(budget.limits, old)
    assert not restored.snapshot()['provider_cost_complete']
