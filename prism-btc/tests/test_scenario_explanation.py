"""Official holding price relations come from frozen references, not prose."""
from copy import deepcopy

import pytest

from live.scenario_explanation import compile_explanation, source_from_structure, rationale_schema


def fixture(side='SHORT'):
    structure = dict(primary={'30m': {'points': [dict(phase='CONFIRMED', is_confirmed=True,
        as_of_ms=1000, high=80590, low=80551.8, close=80570, ma10=80565, ma35=80580)]}},
        levels={'4h.ma10.forming': dict(timeframe='4h', is_confirmed=False, as_of_ms=1100, price=80450.7)})
    source = source_from_structure(structure, dict(mark_price=80580, account_captured_at=1.1))
    context = dict(explanation_contract_version=1, scenario_id='s', positions=[dict(quantity=.1)],
                   side=side, explanation_source=source,
                   current_plan=dict(take_profits=[dict(id='tp1', price=80487.3)]))
    raw = dict(interpretation='80551.8보다 80450.7을 먼저 통과한다는 잘못된 해석',
               evidence_refs=['P:30m:0:low'], comparisons=[
                   dict(level_ref='P:30m:0:low', target_ref='CURRENT:TP:tp1'),
                   dict(level_ref='MA:4h.ma10.forming', target_ref='CURRENT:TP:tp1')])
    return context, raw


def test_short_before_and_after_target_are_source_bound_and_wrong_prose_preserved():
    context, raw = fixture()
    receipt = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert [r['relation'] for r in receipt['comparisons']] == ['BEFORE', 'AFTER']
    assert receipt['raw'] == raw and raw['interpretation'] in receipt['text']
    assert '모델 해석(미검증)' in receipt['text']
    assert receipt['comparisons'][0]['level']['phase'] == 'CONFIRMED'
    assert receipt['comparisons'][1]['level']['as_of_ms'] == 1100
    assert '실제 주문 상태 아님' in receipt['text']


def test_long_reverses_numeric_order_not_source_or_interpretation():
    context, raw = fixture('LONG')
    receipt = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert [r['relation'] for r in receipt['comparisons']] == ['AFTER', 'BEFORE']


def test_equal_is_equal_and_missing_or_retired_frame_is_unknown():
    context, raw = fixture()
    context['current_plan']['take_profits'][0]['price'] = 80551.8
    raw['comparisons'].append(dict(level_ref='P:15m:0:low', target_ref='CURRENT:TP:tp1'))
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert result['comparisons'][0]['relation'] == 'EQUAL'
    assert result['comparisons'][-1]['relation'] == 'UNKNOWN'


def test_adjust_uses_effective_price_and_ref_cannot_cross_candidate():
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    effective = dict(action='ADJUST', take_profits=[dict(id='new', price=80560)])
    saved = deepcopy((context, raw, effective))
    result = compile_explanation(raw, context, effective, scope='c0')
    assert result['comparisons'][0]['relation'] == 'AFTER'
    assert result['comparisons'][0]['target']['price'] == 80560
    assert compile_explanation(raw, context, effective, scope='c1')['comparisons'][0]['relation'] == 'UNKNOWN'
    assert (context, raw, effective) == saved


def test_source_tamper_is_unknown_not_a_new_protection_gate():
    context, raw = fixture()
    context['explanation_source']['points'][0][1] = 42
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert all(r['relation'] == 'UNKNOWN' for r in result['comparisons'])
    assert result['source_status'] == 'UNKNOWN'


@pytest.mark.parametrize('raw', [None, {}, '', 'legacy wrong free text'])
def test_missing_explanation_is_labelled_and_does_not_raise(raw):
    context, _ = fixture()
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert result['raw'] == raw
    assert len(result['text']) <= 2000 and result['text']


def test_schema_has_no_model_prices_sources_or_relations_and_render_is_bounded():
    context, raw = fixture()
    schema = rationale_schema()
    assert set(schema['properties']) == {'interpretation', 'evidence_refs', 'comparisons'}
    assert set(schema['properties']['comparisons']['items']['properties']) == {'level_ref', 'target_ref'}
    raw['interpretation'] = '긴 해석' * 1000
    raw['comparisons'] *= 100
    result = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert result['raw'] == raw and len(result['text']) <= 2000
    assert result['omitted'] is True and '생략' in result['text']


def test_uncalculated_adjust_does_not_publish_raw_target_as_effective():
    from live.scenario_explanation import bind_receipt
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    candidate = dict(id='c0', raw_allocation_proposal=dict(action='ADJUST', rationale=raw,
        take_profits=[dict(id='new', price=80560)]), eligible=False)
    bind_receipt(candidate, context)
    assert candidate['explanation']['comparisons'][0]['relation'] == 'UNKNOWN'
    assert candidate['explanation']['basis'] == 'UNAVAILABLE_EFFECTIVE_PLAN'
    assert 'effective_plan' not in candidate


def test_price_order_survives_volatile_pricing_metadata_only():
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    plan = dict(action='ADJUST', take_profits=[dict(id='new', price=80560)],
                execution_pricing=dict(checked_at=1), risk=dict(total_risk=2))
    first = compile_explanation(raw, context, plan, scope='c0')
    plan['execution_pricing']['checked_at'] = 2
    assert compile_explanation(raw, context, plan, scope='c0') == first
    plan['take_profits'][0]['price'] = 80540
    assert compile_explanation(raw, context, plan, scope='c0')['plan_sha256'] != first['plan_sha256']


def test_explanation_view_cannot_exceed_existing_100kb_input_budget():
    import json
    from live.scenario_explanation import project_input
    from live.scenario_model_input import project_model_input, restore_model_input
    context, _ = fixture()
    payload = dict(market_snapshot={}, contract_context={'explanation_contract_version': 1},
                   response_contract={}, padding='x' * 99_300)
    baseline, _ = project_model_input(payload, allow_gap_rescue=True)
    original = deepcopy(baseline)
    context['explanation_source']['padding'] = 'y' * 5000
    projected = project_input(baseline, context)
    assert len(json.dumps(projected, ensure_ascii=False, separators=(',', ':')).encode()) <= 100_000
    assert restore_model_input(projected)['contract_context']['explanation_source']['status'] == 'OMITTED_INPUT_BUDGET'
    assert baseline == original
