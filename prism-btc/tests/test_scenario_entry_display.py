"""Read-only phase-separated prices, not an entry classifier or trade gate."""
from copy import deepcopy

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_calculated_review import receipt_hash
from live.scenario_entry_display import flat_entry_display
from tests.test_scenario_ma_context import NOW, point, snapshot


def structure():
    market = snapshot(before=point('30m', 2, close=100), after=point('30m', 1, close=110, confirmed=False))
    return build_ma_structure_context(market, {'now': NOW/1000, 'mark_price': 110})


def candidate(side='LONG'):
    c = dict(id='c0', eligible=True, effective_plan=dict(action='OPEN', side=side,
        entries=[dict(price=100)], take_profits=[dict(price=120), dict(price=130)], partial_stops=[]))
    c['receipt_sha256'] = receipt_hash(c)
    return c


def test_confirmed_tail_is_not_forming_close_and_source_is_unchanged():
    facts = structure()
    before = deepcopy(facts)
    result = flat_entry_display(facts)
    assert result['confirmed_close_tail']['30m'] == [[facts['primary']['30m']['points'][0]['as_of_ms'], 100]]
    assert result['confirmed_close_tail']['1h'] is None
    assert facts == before


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_candidate_price_order_ties_all_targets_refs_and_receipt_binding(side):
    facts = structure()
    facts['levels'] = {'1d.ma35.confirmed': {'price': 120}, '12h.ma35.forming': {'price': 125}}
    c = candidate(side)
    before = deepcopy((facts, c))
    result = flat_entry_display(facts, {'candidates': [c]})
    assert result['candidate_price_order'] == [['c0', c['receipt_sha256'], 'AVAILABLE', [
        [100, ['E0']], [120, ['T0', 'M:1d.ma35.confirmed']],
        [125, ['M:12h.ma35.forming']], [130, ['T1']]]]]
    assert (facts, c) == before


@pytest.mark.parametrize('fault', ['receipt', 'value', 'id'])
def test_tampered_receipt_does_not_create_false_order_or_block(fault):
    c = candidate()
    if fault == 'receipt':
        c['receipt_sha256'] = '0'*64
    elif fault == 'value':
        c['effective_plan']['entries'][0]['price'] = 1
    else:
        c['id'] = 'c1'
    display = flat_entry_display(structure(), {'candidates': [c]})
    assert display['candidate_price_order'][0][2:] == ['UNAVAILABLE', None]
    assert display['candidate_price_order_status'] == 'PARTIAL_UNAVAILABLE'


def test_bounds_and_no_tp_are_explicit_not_silent_omissions():
    c = candidate()
    c['effective_plan']['take_profits'] = []
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][3] == [[100,['E0']]]
    assert flat_entry_display(structure(), {'candidates':[c,c,c]})['candidate_price_order_status'] == 'UNAVAILABLE_BOUND'


@pytest.mark.parametrize('fault', ['future', 'stale', 'nonconsecutive'])
def test_invalid_time_source_is_not_relabelled_confirmed(fault):
    market = snapshot()
    path = market['timeframes']['30m']['ma_path']
    if fault == 'future':
        path['confirmed_points'][0]['open_time_ms'] = NOW + 10000000
    elif fault == 'stale':
        path['confirmed_points'] = [point('30m', 4)]
    else:
        path['confirmed_points'] = [point('30m', 4), point('30m', 2)]
    facts = build_ma_structure_context(market, {'now': NOW/1000, 'mark_price':100})
    assert flat_entry_display(facts)['confirmed_close_tail']['30m'] is None


def test_display_helper_is_in_policy_binding_and_loaded_source_parity():
    from live.scenario_provenance import review_policy_binding, _manifest
    assert 'live/scenario_entry_display.py' in review_policy_binding()['source_sha256']
    assert 'live/scenario_entry_display.py' in str(_manifest())


def test_multiple_entries_and_tp_quantities_do_not_modify_price_order():
    c = candidate('SHORT')
    c['effective_plan']['entries'] = [dict(price=120, quantity=.001), dict(price=100, quantity=.009)]
    c['effective_plan']['take_profits'] = [dict(price=100, fraction=0), dict(price=90, fraction=.3)]
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    before = deepcopy(c)
    order = flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][3]
    assert order == [[90,['T1']], [100,['E1','T0']], [120,['E0']]]
    assert c == before


def test_bounded_missing_or_nonfinite_candidate_data_is_unavailable():
    c = candidate()
    c['effective_plan']['entries'] *= 21
    c['receipt_sha256'] = receipt_hash({k:v for k,v in c.items() if k != 'receipt_sha256'})
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][2] == 'UNAVAILABLE'
    c['effective_plan']['entries'][0]['price'] = float('nan')
    assert flat_entry_display(structure(), {'candidates':[c]})['candidate_price_order'][0][2] == 'UNAVAILABLE'


def test_display_preserves_baseline_history_selection_and_all_other_logical_fields():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_model_input import project_model_input, restore_model_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    source['synthetic_size_padding'] = ''
    source['contract_context']['position_flow'] = {'version': 1, 'status': 'AVAILABLE', 'coverage': {},
        'historical_observation_changes': [{'note': 'x' * 2000}]}
    initial, _ = project_model_input(source)
    source['synthetic_size_padding'] = 'x' * (100500 - len(encoded(initial)))
    before = deepcopy(source)
    baseline, _ = project_position_flow_input(source)
    current, _ = project_entry_display_input(source)
    restored = restore_model_input(current)
    assert restored['contract_context'].pop('entry_display')['confirmed_close_tail']['1h'] is None
    assert restored == restore_model_input(baseline)
    assert source == before


def test_nonflat_wrapper_returns_identical_wire_and_audit():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    source = {'market_snapshot': {}, 'contract_context': {'scenario_id': 'active'}}
    assert project_entry_display_input(source) == project_position_flow_input(source)


def test_optional_display_never_blocks_exact_cap_already_v7_baseline():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    baseline, _ = project_position_flow_input(source)
    source['synthetic_size_padding'] = 'x' * (len(source['synthetic_size_padding']) + 100000 - len(encoded(baseline)))
    baseline, audit = project_position_flow_input(source)
    assert len(encoded(baseline)) == 100000
    assert baseline['model_input_projection']['version'] == 7
    result, result_audit = project_entry_display_input(source)
    assert encoded(result) == encoded(baseline)
    assert result_audit == audit


def test_display_does_not_raise_existing_cap_or_hide_oversized_baseline():
    from live.scenario_entry_display import project_entry_display_input
    from live.scenario_position_flow import project_position_flow_input
    from tests.test_scenario_model_input import encoded
    from tests.test_scenario_model_input_gaps import gap_payload
    source = gap_payload()
    source['synthetic_size_padding'] = 'x' * 110000
    assert len(encoded(project_position_flow_input(source)[0])) > 100000
    assert len(encoded(project_entry_display_input(source)[0])) > 100000
