"""Execution deduplication stays on the host, outside judgment payloads."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from core.llm_scenario import ScenarioValidationError, validate_scenario
from live.scenario_calculated_review import _model_payload
from live.scenario_contract import identity_fields, response_schema
from live.scenario_llm import propose
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from tests.test_llm_scenario import sample
from tests.test_scenario_llm import context, wire


def state(name):
    ctx = context(name != 'flat')
    if name == 'pending':
        ctx['positions'] = []
        ctx['pending_entries'] = [dict(id='e1', price=60000, quantity=.001)]
    return ctx


@pytest.mark.parametrize('name', ['flat', 'holding', 'pending'])
@pytest.mark.parametrize('count', [1, 10000])
def test_single_and_calculated_views_do_not_grow_with_host_action_history(name, count):
    ctx = state(name)
    snapshot = dict(valid=True, as_of_ms=1000000)
    contract = response_contract(ctx)
    schema, identity = response_schema(ctx), identity_fields(ctx)

    def requests(current):
        calls = []
        def generate(**request):
            calls.append(request)
            return SimpleNamespace(text=json.dumps(wire(current)))
        propose(snapshot, current, contract, generate=generate, clock=lambda: 1000)
        return [json.loads(calls[0]['user_prompt']),
                _model_payload(snapshot, current, contract),
                _model_payload(snapshot, current, contract, calculated_candidates={'candidates': []})]

    baseline = requests(ctx)
    ctx['seen_action_ids'] = [f'historical_action_{n:025d}' for n in range(count)]
    original = deepcopy(ctx)
    actual = requests(ctx)
    assert ctx == original
    assert response_schema(ctx) == schema and identity_fields(ctx) == identity
    assert actual == baseline
    for payload in actual:
        restored = restore_model_input(payload)
        assert 'seen_action_ids' not in restored['contract_context']


def test_real_two_phase_assembly_omits_ids_without_changing_source():
    from tests.test_scenario_calculated_review import context as calculated_context, run
    ctx = calculated_context()
    ctx['seen_action_ids'] = ['old_action']
    original = deepcopy(ctx)
    _, _, calls = run(ctx)
    assert len(calls) == 2
    assert ctx == original
    for call in calls:
        assert 'seen_action_ids' not in restore_model_input(json.loads(call['user_prompt']))['contract_context']


def test_original_host_still_rejects_duplicate_action_after_model_projection():
    proposal, ctx = sample()
    validate_scenario(proposal, ctx)
    ctx['seen_action_ids'] = [proposal['action_id']]
    original = deepcopy(ctx)
    _model_payload(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx))
    assert ctx == original
    with pytest.raises(ScenarioValidationError, match='duplicate action'):
        validate_scenario(proposal, ctx)
