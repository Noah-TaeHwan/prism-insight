"""Sanitized citation excerpts; no assertion about model decision correctness."""
from copy import deepcopy
import json

import pytest

from live.scenario_explanation_evidence import analyze_explanation


def snapshot():
    # Referenced prices from 23:30/00:20 incidents; small synthetic assembly,
    # not a replay of either complete original account/market packet.
    asof = 1792596016224
    frames = {}
    for frame, duration, close, forming_close in [('15m', 900000, 84668.9, 84353.1),
            ('30m', 1800000, 84549.9, 84374.7), ('1h', 3600000, 84549.9, 83897.4),
            ('1d', 86400000, 82991.0, 83893.7)]:
        boundary = int(asof // duration) * duration
        frames[frame] = dict(status='available', confirmed=dict(open_time_ms=boundary-duration,
            is_confirmed=True, ma10=83314.07999999999, ma35=82634.64),
            recent_confirmed_bars=[dict(open_time_ms=boundary-duration, close=close)],
            forming=dict(open_time_ms=boundary, observed_at_ms=asof-500, is_confirmed=False,
                         ma10=83314.07999999999, ma35=82634.64, ohlcv=dict(close=forming_close)))
    return dict(valid=True, as_of_ms=asof, timeframes=frames)


def context():
    return dict(input_id='fixture', recovery=dict(changed_evidence={
        'timeframes.1h.forming.ohlcv.close': dict(before=83552.8, now=83897.4)}))


def test_typed_reference_official_fact_is_separate_and_source_bound():
    raw = dict(rationale='상승 [[TF:1h:forming:close]]', price=1)
    snap, ctx = snapshot(), context()
    original = deepcopy((raw, snap, ctx))
    result = analyze_explanation(raw, snap, ctx)
    fact = result['annotations'][0]
    assert fact['status'] == 'VERIFIED_REFERENCE' and fact['value'] == 83897.4
    assert fact['frame'] == '1h' and fact['phase'] == 'forming'
    assert fact['observed_at_ms'] == snap['as_of_ms']-500
    assert '83897.4' in fact['canonical_fact'] and '호스트' in fact['canonical_fact']
    assert len(result['snapshot_sha256']) == 64
    assert result['authoritative_for_trading'] is False
    assert result['free_prose_verified'] is False
    assert (raw, snap, ctx) == original


@pytest.mark.parametrize('token', ['[[TF:30m:confirmed:close]]', '[[TF:1h:forming:ma10]]', '[[TF:1h:confirmed:ma35]]'])
def test_supported_phase_field_references(token):
    assert analyze_explanation(dict(rationale=token), snapshot(), context())['annotations'][0]['status'] == 'VERIFIED_REFERENCE'


def test_reference_time_distinguishes_bar_close_from_actual_observation():
    snap = snapshot()
    result = analyze_explanation(dict(rationale='[[TF:30m:confirmed:close]] [[TF:1h:forming:close]]'), snap, context())
    closed, observed = result['annotations']
    assert closed['time_kind'] == 'BAR_CLOSE'
    assert closed['observed_at_ms'] == snap['timeframes']['30m']['confirmed']['open_time_ms']+1800000
    assert '봉 종료시각' in closed['canonical_fact']
    assert observed['time_kind'] == 'OBSERVATION'
    assert observed['observed_at_ms'] == snap['timeframes']['1h']['forming']['observed_at_ms']
    assert '관측시각' in observed['canonical_fact']
    assert closed['snapshot_as_of_ms'] == observed['snapshot_as_of_ms'] == snap['as_of_ms']


def test_explicit_unconfirmed_bar_cannot_supply_confirmed_close():
    snap = snapshot()
    snap['timeframes']['30m']['recent_confirmed_bars'][0]['is_confirmed'] = False
    fact = analyze_explanation(dict(rationale='[[TF:30m:confirmed:close]]'), snap, context())['annotations'][0]
    assert fact['status'] == 'UNVERIFIED' and 'value' not in fact


@pytest.mark.parametrize('scope', ['frame', 'point'])
def test_explicit_invalid_source_is_not_a_verified_fact(scope):
    snap = snapshot()
    target = snap['timeframes']['30m']
    if scope == 'point':
        target = target['confirmed']
    target['valid'] = False
    fact = analyze_explanation(dict(rationale='[[TF:30m:confirmed:close]]'), snap, context())['annotations'][0]
    assert fact['status'] == 'UNVERIFIED' and 'value' not in fact


@pytest.mark.parametrize('fault', ['missing', 'future', 'phase_mismatch', 'bad_frame', 'malformed'])
def test_bad_or_future_reference_unverified_no_fallback(fault):
    snap = snapshot()
    token = '[[TF:30m:confirmed:close]]'
    if fault == 'missing':
        snap['timeframes']['30m'].pop('recent_confirmed_bars')
    elif fault == 'future':
        snap['timeframes']['30m']['confirmed']['open_time_ms'] = snap['as_of_ms']
    elif fault == 'phase_mismatch':
        snap['timeframes']['30m']['confirmed']['open_time_ms'] -= 1800000
    elif fault == 'bad_frame':
        token = '[[TF:5m:confirmed:close]]'
    else:
        token = '[[TF:1h:forming:close'
    a = analyze_explanation(dict(rationale=token), snap, context())['annotations'][0]
    assert a['status'] == 'UNVERIFIED'
    assert 'value' not in a


def test_incident_2330_wrong_frame_value_and_rounded_correct_ma():
    text = ('변경 경로 `timeframes.1h.forming.ohlcv.close`는 83,552.8→83,893.7, '
            '1h 형성 MA10 83,314.08입니다.')
    result = analyze_explanation(dict(rationale=text), snapshot(), context())
    a, b = result['annotations']
    assert a['status'] == 'MISMATCH' and a['cited_value'] == 83893.7 and a['value'] == 83897.4
    assert a['cited_before'] == 83552.8 and a['actual_before'] == 83552.8
    assert b['status'] == 'MATCH'


def test_incident_0020_draft_wait_wrong_confirmed_frame_and_no_candidate_reason_separate():
    parsed = dict(wait_proposal=dict(rationale='1h 확정 종가 84,549.9와 30m 확정 종가 84,668.9는 위입니다.'),
                  no_candidate_reason='보호된 잔여 물량을 유지합니다.', candidates=[])
    result = analyze_explanation(parsed, snapshot(), context())
    assert [a['status'] for a in result['annotations']] == ['MATCH', 'MISMATCH']
    assert all(a['prose_path'] == 'wait_proposal.rationale' for a in result['annotations'])
    assert result['annotations'][1]['value'] == 84549.9


def test_incident_0920_and_correct_other_frame_counterexample():
    snap, ctx = snapshot(), context()
    snap['timeframes']['1h']['forming']['ohlcv']['close'] = 83123.1
    ctx['recovery']['changed_evidence']['timeframes.1h.forming.ohlcv.close'] = dict(before=82811.8, now=83123.1)
    result = analyze_explanation(dict(rationale='timeframes.1h.forming.ohlcv.close 82811.8→83133.6; 1d 형성 종가 83893.7'), snap, ctx)
    assert [a['status'] for a in result['annotations']] == ['MISMATCH', 'MATCH']


@pytest.mark.parametrize('text', ['1h 및 30m 확정 종가 84549.9', '1h 확정 종가 84000~85000',
                                '목표 85000, 진입 84000', '1h 형성 종가 8만'])
def test_ranges_joint_frames_and_order_numbers_are_not_inferred(text):
    assert analyze_explanation(dict(rationale=text), snapshot(), context())['annotations'] == []


@pytest.mark.parametrize('text', [
    '직전 관찰에서 1h forming close는 83483.4에서 현재 83460.0으로 낮아졌다',
    'timeframes.15m.confirmed.ma10도 83388.53에서 83427.22로',
    '15m 확정 종가는 83714.0에서 83630.0으로',
    '1h 확정 종가는83469.8에서83791.7로',
])
def test_historical_korean_from_to_is_unassessed_not_false_current_mismatch(text):
    # Four normal excerpts from slots 5975280/86/5301/04. Their first numbers
    # refer to earlier observations, not the current frozen snapshot value.
    result = analyze_explanation(dict(rationale=text), snapshot(), context())
    assert result['annotations'] == []
    assert result['unassessed_prose_count'] == 1


def test_single_frame_arrow_does_not_assume_recovery_baseline_for_previous_value():
    result = analyze_explanation(dict(rationale='1h 형성 종가 83000→83897.4'), snapshot(), context())
    annotation = result['annotations'][0]
    assert annotation['status'] == 'MATCH'
    assert annotation['before_status'] == 'UNASSESSED'
    assert 'actual_before' not in annotation


@pytest.mark.parametrize('fault', ['future_observation', 'synthetic', 'unavailable_frame', 'missing_ma'])
def test_forming_references_require_original_available_observation(fault):
    snap = snapshot()
    frame = snap['timeframes']['1h']
    if fault == 'future_observation':
        frame['forming']['observed_at_ms'] = snap['as_of_ms']+1
    elif fault == 'synthetic':
        frame['forming']['observation_kind'] = 'synthetic_boundary'
    elif fault == 'unavailable_frame':
        frame['status'] = 'unavailable'
    else:
        frame['forming'].pop('ma10')
    annotation = analyze_explanation(dict(reason='[[TF:1h:forming:ma10]]'), snap, context())['annotations'][0]
    assert annotation['status'] == 'UNVERIFIED' and 'value' not in annotation


def test_baseline_and_snapshot_now_are_separate_and_rounding_is_not_cross_frame_substitution():
    ctx = context()
    # A conflicting redundant now must never replace the snapshot's actual value.
    ctx['recovery']['changed_evidence']['timeframes.1h.forming.ohlcv.close']['now'] = 83893.7
    result = analyze_explanation(dict(reason='timeframes.1h.forming.ohlcv.close 83552.8→83893.7'), snapshot(), ctx)
    assert result['annotations'][0]['status'] == 'MISMATCH'
    good = analyze_explanation(dict(reason='1h 형성 종가 83897'), snapshot(), ctx)
    assert good['annotations'][0]['status'] == 'MATCH'


def test_ambiguous_joint_frame_order_targets_and_unapproved_keys_unassessed():
    parsed = dict(rationale='1h·30m 확정 종가 84549.9; 진입 84000 TP 85000; 1h 가격은 84000',
                  arbitrary='[[TF:1h:forming:close]]', entries=[dict(price=1)])
    result = analyze_explanation(parsed, snapshot(), context())
    assert result['annotations'] == []
    assert result['unassessed_prose_count'] == 1


def test_caps_and_missing_snapshot_do_not_claim_complete_validation():
    result = analyze_explanation(dict(rationale='[[TF:1h:forming:close]] '*1000), {}, {})
    assert result['truncated'] is True
    assert len(result['annotations']) <= 32
    assert result['free_prose_verified'] is False


@pytest.mark.parametrize('citation', ['1h 형성 종가 83897.499', '[[TF:1h:forming:close]]'])
def test_character_budget_never_parses_a_cut_numeric_or_token_field(citation):
    from live.scenario_explanation_evidence import MAX_CHARS
    prefix = 'x'*(MAX_CHARS-len(citation)+2)
    result = analyze_explanation(dict(reason='정상 설명', rationale=prefix+citation,
                                     invalidation='[[TF:30m:confirmed:close]]'), snapshot(), context())
    assert result['annotations'] == []
    assert result['truncated'] is True
    assert result['unassessed_prose_count'] == 3


def test_invalid_close_end_and_oversized_numeric_citation_are_unverified():
    snap = snapshot()
    snap['timeframes']['30m']['recent_confirmed_bars'][0]['close_time_ms'] = snap['as_of_ms']+1
    a = analyze_explanation(dict(rationale='[[TF:30m:confirmed:close]]'), snap, context())['annotations'][0]
    assert a['status'] == 'UNVERIFIED'
    result = analyze_explanation(dict(rationale='1h 형성 종가 '+('9'*1000)), snapshot(), context())
    assert result['annotations'][0]['status'] == 'UNVERIFIED'
    json.dumps(result, allow_nan=False)


def test_analyzer_is_not_in_replay_or_trading_path_even_if_it_fails(monkeypatch):
    from live import scenario_explanation_evidence as module
    from tests.test_scenario_calculated_review import run
    normal, normal_events, normal_calls = run()
    def fail(*args, **kwargs):
        raise RuntimeError('must not alter authority')
    monkeypatch.setattr(module, 'analyze_explanation', fail)
    failed, events, calls = run()
    assert normal == failed and calls == normal_calls
    raw_before = [e for e in normal_events if e['kind'] == 'MODEL_RAW']
    raw_after = [e for e in events if e['kind'] == 'MODEL_RAW']
    assert len(raw_after) == 2
    for a, b in zip(raw_before, raw_after):
        assert 'explanation_evidence' not in b
        assert a == b
    assert all(json.loads(e['raw']) for e in raw_after)


def test_no_selection_wait_and_schema_failure_remain_unchanged(monkeypatch):
    from live import scenario_explanation_evidence as module
    from tests.test_scenario_calculated_review import run
    def fail(*args, **kwargs):
        raise RuntimeError('display-only failure')
    monkeypatch.setattr(module, 'analyze_explanation', fail)
    result, events, calls = run(candidates=[])
    assert len(calls) == 1 and result['selection']['selection'] == 'WAIT'
    assert all('explanation_evidence' not in e for e in events)
    def corrupt(reply, *_args):
        reply['unexpected_field'] = True
    with pytest.raises(ValueError, match='research_response_contract'):
        run(mutate=corrupt)
