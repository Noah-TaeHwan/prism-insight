"""Narrow exact gap reuse; no facts, candidates, or risk fields are removed."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.scenario_model_input import project_model_input, restore_model_input
from tests.test_scenario_model_input import table_payload, encoded


def gap_payload():
    source = table_payload()
    source['synthetic_size_padding'] = 'x' * 100000
    frames = source['market_snapshot']['timeframes']
    primary = source['contract_context']['ma_structure'] = {'primary': {}}
    for tf in ('15m', '30m', '1h'):
        gaps = {kind: {'exact_values': [True, 1, 1.0, '관측😀'] * 30, 'kind': kind}
                for kind in ('confirmed_gap', 'forming_gap')}
        frames.setdefault(tf, {})['ma_path'] = deepcopy(gaps)
        primary['primary'][tf] = deepcopy(gaps)
    return source


def test_v7_six_exact_gaps_roundtrip_without_mutating_source():
    source = gap_payload()
    before = encoded(source)
    legacy, _ = project_model_input(source)
    assert legacy['model_input_projection']['version'] == 2
    assert len(encoded(legacy)) > 100000
    projected, audit = project_model_input(source, allow_gap_rescue=True)
    assert projected['model_input_projection']['version'] == 7
    assert projected['model_input_projection']['prior_projection_version'] == 2
    assert len(projected['model_input_projection']['ma_gap_references']) == 6
    assert encoded(source) == before
    assert encoded(restore_model_input(projected)) == before
    assert audit['projected_utf8_bytes'] < audit['original_utf8_bytes']


@pytest.mark.parametrize('value', [1, 1.0, False])
def test_numeric_types_are_not_interchangeable(value):
    source = gap_payload()
    source['contract_context']['ma_structure']['primary']['15m']['confirmed_gap']['exact_values'][0] = value
    projected, _ = project_model_input(source, allow_gap_rescue=True)
    assert '15m.confirmed_gap' not in projected['model_input_projection']['ma_gap_references']
    assert encoded(restore_model_input(projected)) == encoded(source)


@pytest.mark.parametrize('fault', ['source', 'unknown_path', 'duplicate', 'missing', 'unknown_version',
                                 'bool_version', 'marker', 'extra_marker', 'hash', 'unregistered'])
def test_invalid_gap_references_fail_closed(fault):
    projected, _ = project_model_input(gap_payload(), allow_gap_rescue=True)
    meta = projected['model_input_projection']
    target = projected['contract_context']['ma_structure']['primary']['15m']
    if fault == 'source':
        projected['market_snapshot']['timeframes']['15m']['ma_path']['confirmed_gap']['kind'] = 'changed'
    elif fault == 'unknown_path':
        meta['ma_gap_references'][0] = 'contract_context.positions'
    elif fault == 'duplicate':
        meta['ma_gap_references'].append(meta['ma_gap_references'][0])
    elif fault == 'missing':
        meta['ma_gap_references'].pop()
    elif fault == 'unknown_version':
        meta['prior_projection_version'] = 7
    elif fault == 'bool_version':
        meta['prior_projection_version'] = True
    elif fault == 'marker':
        target['confirmed_gap']['$ma_gap'] = '1h.confirmed_gap'
    elif fault == 'extra_marker':
        target['confirmed_gap']['extra'] = 1
    elif fault == 'hash':
        meta['prior_projection_sha256'] = '0' * 64
    else:
        projected['other'] = {'$ma_gap': '15m.confirmed_gap'}
    with pytest.raises(ValueError):
        restore_model_input(projected)


def test_reserved_gap_marker_rejected_and_nonprofitable_source_not_inflated():
    with pytest.raises(ValueError, match='reserved_projection_field'):
        project_model_input({'x': {'$ma_gap': '15m.confirmed_gap'}})
    with pytest.raises(ValueError):
        restore_model_input({'x': {'$ma_gap': '15m.confirmed_gap'}})
    source = gap_payload()
    for frame in source['market_snapshot']['timeframes'].values():
        for kind in ('confirmed_gap', 'forming_gap'):
            if 'ma_path' in frame:
                frame['ma_path'][kind] = {'small': 1}
    for frame in source['contract_context']['ma_structure']['primary'].values():
        for kind in frame:
            frame[kind] = {'small': 1}
    projected, _ = project_model_input(source, allow_gap_rescue=True)
    assert projected['model_input_projection']['version'] == 2
    assert encoded(restore_model_input(projected)) == encoded(source)


def test_previously_callable_payload_keeps_prior_projection_and_exact_wire():
    source = gap_payload()
    source.pop('synthetic_size_padding')
    projected, _ = project_model_input(source, allow_gap_rescue=True)
    assert len(encoded(projected)) <= 100000
    assert projected['model_input_projection']['version'] == 2
    assert encoded(restore_model_input(projected)) == encoded(source)


def test_dictionary_key_order_is_nonsemantic_but_array_order_is_preserved():
    source = gap_payload()
    primary = source['contract_context']['ma_structure']['primary']
    original = primary['15m']['confirmed_gap']
    primary['15m']['confirmed_gap'] = dict(reversed(list(original.items())))
    projected, audit = project_model_input(source, allow_gap_rescue=True)
    assert '15m.confirmed_gap' in projected['model_input_projection']['ma_gap_references']
    restored = restore_model_input(projected)
    canonical = json.dumps(restored, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == audit['original_semantic_sha256']
    original['exact_values'].reverse()
    assert restored['contract_context']['ma_structure']['primary']['15m']['confirmed_gap']['exact_values'] != original['exact_values']


def test_different_array_order_is_not_shared():
    source = gap_payload()
    source['contract_context']['ma_structure']['primary']['15m']['confirmed_gap']['exact_values'].reverse()
    projected, _ = project_model_input(source, allow_gap_rescue=True)
    assert '15m.confirmed_gap' not in projected['model_input_projection']['ma_gap_references']
    assert encoded(restore_model_input(projected)) == encoded(source)


def test_v7_wraps_existing_v6_schema_projection_without_changing_api_contract():
    source = gap_payload()
    source['contract_context']['entry_contract_version'] = 2
    repeated = {'type': 'object', 'properties': {f'field{i}': {'type': 'string', 'description': 'x' * 100}
                                               for i in range(10)}}
    source['response_contract'] = {'first': repeated, 'second': deepcopy(repeated)}
    projected, audit = project_model_input(source, allow_gap_rescue=True)
    assert projected['model_input_projection']['version'] == 7
    assert projected['model_input_projection']['prior_projection_version'] == 6
    restored = restore_model_input(projected)
    assert restored['response_contract'] == source['response_contract']
    canonical = json.dumps(restored, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == audit['original_semantic_sha256']


def test_pre_v7_successful_review_tape_replays_with_current_projector(monkeypatch):
    from live import scenario_model_input as module
    from live.scenario_review_guard import guard_review
    from tests.test_scenario_entry_contract_wire import taped
    factor = module._factor_ma_gaps
    monkeypatch.setattr(module, '_factor_ma_gaps', lambda _: False)
    args, calls = taped()
    assert args[3]['outcome']['status'] == 'COMPLETE'
    monkeypatch.setattr(module, '_factor_ma_gaps', factor)
    fresh = dict(args[1], now=1001, account_captured_at=1001)
    checked = guard_review(*args, fresh, bindings={'test': 'entry-contract'},
                           research_mode='economic-reject-calculated-v1')
    assert checked['payload']['action'] == 'EXIT'
    assert len(calls) == 2


def test_old_history_trim_success_keeps_exact_final_request(monkeypatch):
    from live import scenario_model_input as module
    from live.scenario_position_flow import project_position_flow_input
    source = gap_payload()
    source['synthetic_size_padding'] = ''
    source['contract_context']['position_flow'] = {
        'version': 1, 'status': 'AVAILABLE', 'coverage': {},
        'historical_observation_changes': [{'note': 'x' * 2000}]}
    factor = module._factor_ma_gaps
    monkeypatch.setattr(module, '_factor_ma_gaps', lambda _: False)
    initial, _ = project_model_input(source)
    source['synthetic_size_padding'] = 'x' * (100500 - len(encoded(initial)))
    old, _ = project_position_flow_input(source)
    assert len(encoded(old)) <= 100000
    assert restore_model_input(old)['contract_context']['position_flow']['coverage']['model_budget_omitted_items'] == 1
    monkeypatch.setattr(module, '_factor_ma_gaps', factor)
    current, _ = project_position_flow_input(source)
    assert encoded(current) == encoded(old)
