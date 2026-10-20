"""Policy delivery/immutability checks; scripted replies do not prove LLM skill."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.scenario_calculated_review import DRAFT_PROMPT, SELECT_PROMPT, calculate_candidate, research_review
from live.scenario_llm import MA_STRUCTURE_PROMPT
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, run, wire
from tests.test_scenario_entry_contract_wire import definition


def test_target_consistency_policy_is_shared_without_automatic_ma_cap():
    shared = ' '.join(MA_STRUCTURE_PROMPT.split())
    for clause in (
        'Check each TP against its cited reference',
        'timeframe, MA, confirmed/forming phase and value',
        'original premise and expected_progress',
        'one target before a level does not put every target before it',
        'supported breakout-extension thesis remains allowed',
        'not an automatic MA cap or a new exit trigger',
    ):
        assert clause in shared
    assert 'Apply the shared TP/reference/thesis consistency check' in DRAFT_PROMPT
    for clause in ('immutable original', 'effective targets', 'another consistent candidate or WAIT',
                   'Do not repair the original contract by rewriting selection rationale'):
        assert clause in SELECT_PROMPT


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['confirmed', 'forming'])
@pytest.mark.parametrize('extension', [False, True])
def test_mixed_targets_remain_unmodified_no_prose_gate_or_automatic_cap(side, phase, extension):
    ctx = context()
    proposal = wire(ctx, side)
    direction = 1 if side == 'LONG' else -1
    level = 83410 + direction * 400
    proposal['take_profits'] = [
        dict(id='near', price=83410 + direction * 200, fraction=.5),
        dict(id='far', price=83410 + direction * 600, fraction=.5),
    ]
    proposal['rationale'] = f'1d MA35 {phase} {level}; ' + (
        'supported breakout-extension thesis' if extension else 'all targets before the level')
    original = deepcopy(proposal)
    receipt = calculate_candidate(proposal, ctx, candidate_id='c0')
    result, _, calls = run(ctx, [proposal])
    assert len(calls) == 2
    assert MA_STRUCTURE_PROMPT in calls[0]['system_prompt']
    assert MA_STRUCTURE_PROMPT in calls[1]['system_prompt']
    assert DRAFT_PROMPT in calls[0]['system_prompt']
    assert SELECT_PROMPT in calls[1]['system_prompt']
    assert result['selected']['raw_proposal'] == original
    assert result['selected']['raw_sha256'] == receipt['raw_sha256']
    assert result['selected']['effective_sha256'] == receipt['effective_sha256']
    assert proposal == original
    assert result['orders_submitted'] == 0
    assert all(len(call['user_prompt'].encode()) <= 100000 for call in calls)


def test_incident_target_bound_and_original_contract_survive_select_wait():
    # Exact incident price/thesis subset, not a replay of its recovery/account state.
    ctx = context()
    ctx.update(entry_contract_version=1, mark_price=82113.6,
               execution_price_policy=dict(version='round-limit-v1'),
               limit_price_quotes=dict(bid=82113.5, ask=82113.6, captured_at=1000))
    candidate, wait = wire(ctx, 'LONG'), wire(ctx)
    candidate.update(entries=[dict(id='e', price=82300, quantity=.01)], hard_stop=81700,
        take_profits=[dict(id='near', price=82500, fraction=.5),
                      dict(id='far', price=82900, fraction=.5)],
        rationale='1d MA35 82806.19 및 12h MA35 83192.58 저항 그 아래',
        entry_contract=definition(), thesis_assessment=None)
    candidate['entry_contract']['expected_progress'] = '1d MA35 82806 부근 전까지'
    candidate['entry_contract']['explicit_failure']['price'] = 81700
    wait.update(entry_contract=None, thesis_assessment=None)
    original = deepcopy(candidate)
    expected = calculate_candidate(candidate, ctx, candidate_id='c0')
    assert expected['eligible']
    assert expected['effective_plan']['take_profits'][1]['price'] == 82892.7
    assert 82500 < 82806.19 < 82892.7 < 82992 < 83192.58
    calls = []

    def generate(**request):
        calls.append(request)
        payload = restore_model_input(json.loads(request['user_prompt']))
        if len(calls) == 1:
            reply = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason='')
        else:
            receipt = payload['calculated_candidates']['candidates'][0]
            assert receipt['raw_proposal'] == original
            assert receipt['raw_sha256'] == expected['raw_sha256']
            assert receipt['effective_sha256'] == expected['effective_sha256']
            assert receipt['effective_plan']['take_profits'] == expected['effective_plan']['take_profits']
            chosen = payload['calculated_candidates']['wait']
            reply = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}",
                         rationale='원래 목표 설명과 먼 TP가 맞지 않아 관망')
        return SimpleNamespace(text=json.dumps(reply))

    result = research_review(dict(valid=True, as_of_ms=1000000), ctx,
        response_contract(ctx), generate=generate, record=lambda _: None, clock=lambda: 1000)
    assert len(calls) == 2 and result['selected']['id'] == 'WAIT'
    assert candidate == original and result['orders_submitted'] == 0
