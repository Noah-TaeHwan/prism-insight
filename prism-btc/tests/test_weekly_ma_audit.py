"""Explicit weekly prefix claim only; no free-prose correctness guarantee."""
from copy import deepcopy

import pytest

from live.scenario_explanation_evidence import analyze_explanation


def inputs():
    asof = 1792661400000
    return dict(valid=True, as_of_ms=asof, timeframes={'1w': dict(forming=dict(
        is_confirmed=False, open_time_ms=asof-3600000, observed_at_ms=asof,
        ma10=81204.3, ohlcv=dict(close=81738.8)))}), dict(mark_price=81738.8)


def inspect(text):
    snap, ctx = inputs()
    before = deepcopy((snap, ctx))
    result = analyze_explanation(dict(rationale=text), snap, ctx)
    assert (snap, ctx) == before
    assert result['authoritative_for_trading'] is False and result['free_prose_verified'] is False
    return result['annotations']


def test_actual_weekly_prefix_has_official_fact_and_unassessed_price_basis():
    rows = inspect('잔여분 목표는 4h MA35 아래 현 가격 위쪽의 주봉 MA10 forming(81204.3) 반응 구간 직전인 81307.3에 둔다.')
    numeric = next(a for a in rows if a['kind'] == 'EXPLICIT_NUMERIC_CITATION')
    position = next(a for a in rows if a['kind'] == 'MA_MARK_POSITION')
    assert numeric['status'] == 'MATCH' and numeric['value'] == 81204.3
    assert position['status'] == 'UNASSESSED'
    assert position['frame'] == '1w' and position['phase'] == 'forming' and position['field'] == 'ma10'
    assert position['comparison_value'] == 81738.8 and position['actual_position'] == '아래'
    assert position['cited_position'] == '위'


@pytest.mark.parametrize('position,status', [('위쪽', 'MISMATCH'), ('아래쪽', 'MATCH')])
def test_explicit_mark_reference_is_mechanically_assessable(position, status):
    rows = inspect(f'MarkPrice {position}의 주봉 MA10 forming(81204.3) 반응 구간이다.')
    assert next(a for a in rows if a['kind'] == 'MA_MARK_POSITION')['status'] == status


@pytest.mark.parametrize('text', [
    '예상: 현 가격 위쪽의 주봉 MA10 forming(81204.3) 반응 구간이다.',
    '향후 현 가격 위쪽의 주봉 MA10 forming(81204.3) 반응을 예상한다.',
    '현 가격 위쪽의 주봉 MA10 forming(81204.3)이 된다면 재검토한다.',
    '현 가격 위쪽의 주봉 MA10 forming(81204.3)은 아니다.',
    '과거 현 가격 위쪽의 주봉 MA10 forming(81204.3) 반응 구간이었다.',
    '현 가격 위쪽으로 오르면 주봉 MA10 forming(81204.3)을 검토한다.',
    '진입가 위쪽의 주봉 MA10 forming(81204.3) 반응 구간이다.',
    '현 가격 위쪽의 주봉과 일봉 MA10 forming(81204.3) 반응 구간이다.',
    '현 가격 위쪽의 주봉 MA10(81204.3) 반응 구간이다.',
    '현 가격 위쪽의 주봉 MA10 confirmed(81204.3) 반응 구간이다.',
    '현 가격 위쪽의 4h MA10 forming(81204.3) 반응 구간이다.',
    '현 가격 위쪽의 주봉 MA10 forming(81204.3e2) 반응 구간이다.',
])
def test_other_basis_future_or_ambiguous_labels_are_unassessed(text):
    assert not any(a.get('frame') == '1w' for a in inspect(text))


def test_invalid_source_cannot_supply_official_fact():
    snap, ctx = inputs()
    snap['timeframes']['1w']['forming']['observed_at_ms'] += 1
    rows = analyze_explanation(dict(rationale='현 가격 위쪽의 주봉 MA10 forming(81204.3)'), snap, ctx)['annotations']
    assert rows and all(a['status'] == 'UNVERIFIED' and 'canonical_fact' not in a for a in rows)


def test_missing_mark_keeps_numeric_fact_but_cannot_assess_position():
    snap, _ = inputs()
    rows = analyze_explanation(dict(rationale='현 가격 위쪽의 주봉 MA10 forming(81204.3)'), snap, {})['annotations']
    assert next(a for a in rows if a['kind'] == 'EXPLICIT_NUMERIC_CITATION')['status'] == 'MATCH'
    assert next(a for a in rows if a['kind'] == 'MA_MARK_POSITION')['status'] == 'UNVERIFIED'
