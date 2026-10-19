"""Gap-path input and policy regressions, not model skill or profit evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import research_review
from live.scenario_entry_contract import entry_contract_schema
from live.scenario_llm import MA_STRUCTURE_PROMPT, SYSTEM_PROMPT
from live.scenario_model_input import restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, wire
from tests.test_scenario_ma_context import NOW, point


def gap_snapshot(side, provisional=True, *, stationary=False, include_4h=True):
    direction = 1 if side == 'LONG' else -1
    frames = {}
    for frame in ('1h', '4h') if include_4h else ('1h',):
        offset = 2 if provisional else 3
        before = point(frame, offset, close=83410 - direction * 120,
                       fast=83410 - direction * 110, slow=83410 + direction * 490)
        after = point(frame, offset - 1,
                      close=before['close'] if stationary else 83410,
                      fast=83410 - direction * 130, slow=83410 + direction * 510,
                      confirmed=not provisional)
        frames[frame] = dict(ma_path=dict(version=1,
            confirmed_points=[before] if provisional else [before, after],
            forming_point=after if provisional else None))
    return dict(valid=True, as_of_ms=NOW, timeframes=frames)


def test_gap_policy_keeps_bounded_thesis_and_existing_host_authority():
    policy = ' '.join(MA_STRUCTURE_PROMPT.split())
    for clause in (
        'gap is still WIDENING',
        'LONG: MA10 < MA35, actual price rises from below MA10',
        'SHORT: MA10 > MA35, actual price falls from above MA10',
        'Do not require convergence, a divergence peak, an MA-order crossover, full trend reversal',
        'prior MA35 touch/rejection, another closed bar or an additional retest',
        'Width or WIDENING alone is insufficient',
        'moving MA passing stationary price',
        'prefer 1h and available 4h over 30m',
        'does not replace the global directional priority',
        "market_snapshot.timeframes['4h'].ma_path own-time points",
        'host-calculated costs and structural invalidation',
        'size within the existing risk cap',
        'No fixed gap threshold or invented probability',
        'use existing BREAKOUT regime',
        'only authoritative numeric failure rule',
        'do not assume extension beyond MA35',
        'Arrival/touch alone is not automatic FAILED or EXIT',
        'Never relabel pre-existing or legacy holdings',
        'SHORT: rebound above MA10, failed advance at MA35',
        'LONG: pullback below MA10, defense at MA35',
    ):
        assert clause in policy
    assert entry_contract_schema()['properties']['regime']['enum'] == ['TREND', 'BREAKOUT', 'RANGE']
    assert '1h > 30m > 4h > 12h > 1d > 1w' in SYSTEM_PROMPT


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('provisional', [False, True])
def test_widening_price_crossing_survives_both_actual_review_requests(side, provisional):
    ctx = context()
    ctx.update(now=NOW / 1000, input_captured_at=NOW / 1000, account_captured_at=NOW / 1000)
    snap = gap_snapshot(side, provisional)
    original = deepcopy((snap, ctx))
    calls = []

    def generate(**request):
        calls.append(request)
        assert MA_STRUCTURE_PROMPT in request['system_prompt']
        assert len(request['user_prompt'].encode()) <= 100000
        payload = restore_model_input(json.loads(request['user_prompt']))
        assert payload['market_snapshot']['timeframes']['4h']['ma_path'] == snap['timeframes']['4h']['ma_path']
        facts = payload['contract_context']['ma_structure']
        step = facts['primary']['1h']['transitions'][-1]
        assert step['gap_state'] == 'WIDENING'
        assert step['gap_change_price'] == 40
        assert step['from_position'] == ('BELOW' if side == 'LONG' else 'ABOVE')
        assert step['to_position'] == 'BETWEEN'
        assert step['close_change_price'] == (120 if side == 'LONG' else -120)
        assert step['provisional'] is provisional
        assert facts['higher_frames']['4h']['latest_structure']['price_position'] == 'BETWEEN'
        if len(calls) == 1:
            candidate, wait = wire(ctx, side), wire(ctx)
            candidate['take_profits'][0]['price'] = 83880 if side == 'LONG' else 82940
            candidate['expires_at'] = wait['expires_at'] = ctx['now'] + 100
            reply = dict(wait_proposal=wait, candidates=[candidate], no_candidate_reason='')
        else:
            candidate = next(c for c in payload['calculated_candidates']['candidates'] if c['eligible'])
            reply = dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}", rationale='fixture')
        return SimpleNamespace(text=json.dumps(reply))

    result = research_review(snap, ctx, response_contract(ctx), generate=generate,
                             record=lambda _: None, clock=lambda: ctx['now'])
    assert len(calls) == 2
    assert result['selected']['raw_proposal']['side'] == side
    assert result['orders_submitted'] == 0 and result['executable'] is False
    assert (snap, ctx) == original


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_stationary_price_and_missing_4h_never_become_crossing_evidence(side):
    snap = gap_snapshot(side, stationary=True, include_4h=False)
    facts = build_ma_structure_context(snap, dict(now=NOW / 1000, mark_price=83410))
    step = facts['primary']['1h']['transitions'][-1]
    assert step['gap_state'] == 'WIDENING'
    assert step['to_position'] == 'BETWEEN'
    assert step['close_change_price'] == 0
    assert facts['higher_frames']['4h']['status'] == 'unavailable'
    assert facts['higher_frames']['4h']['latest_structure'] is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('status', ['INTACT', 'FAILED'])
def test_existing_holding_contract_is_not_replaced_by_new_gap_candidate(side, status):
    from tests.test_scenario_entry_contract_wire import context as holding_context, taped
    ctx = holding_context(side)
    original = deepcopy(ctx['registered_entry_contract'])
    args, calls = taped(ctx, status=status, choose_wait=status == 'INTACT')
    assert args[3]['outcome']['status'] == 'COMPLETE'
    for call in calls:
        assert MA_STRUCTURE_PROMPT in call['system_prompt']
        payload = restore_model_input(json.loads(call['user_prompt']))
        assert payload['contract_context']['registered_entry_contract'] == original
    selected = args[3]['outcome']['result']['selected']['raw_proposal']
    assert selected['entry_contract'] is None
    assert selected['action'] == ('EXIT' if status == 'FAILED' else 'WAIT')
