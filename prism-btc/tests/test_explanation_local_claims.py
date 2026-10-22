"""Bounded sanitized excerpts, not a replay or proof of model reasoning."""
from copy import deepcopy

import pytest

from live.scenario_explanation_evidence import analyze_explanation


def inputs():
    asof = 1792641000000
    frames = {}
    for frame, duration in [('12h', 43200000), ('1h', 3600000), ('30m', 1800000), ('4h', 14400000)]:
        boundary = asof // duration * duration
        frames[frame] = dict(confirmed=dict(is_confirmed=True, open_time_ms=boundary-duration,
            ma10=82328.57, ma35=82209.26),
            recent_confirmed_bars=[dict(open_time_ms=boundary-2*duration, close=82915.9),
                                   dict(open_time_ms=boundary-duration, close=82782.3)],
            forming=dict(is_confirmed=False, open_time_ms=boundary, observed_at_ms=asof,
                         ma10=82463.27, ma35=82197.9171, ohlcv=dict(close=82200.23)))
    return dict(valid=True, as_of_ms=asof, timeframes=frames), dict(mark_price=82200.23)


def annotations(text):
    snap, context = inputs()
    before = deepcopy((snap, context))
    result = analyze_explanation(dict(rationale=text), snap, context)
    assert (snap, context) == before
    assert result['authoritative_for_trading'] is False and result['free_prose_verified'] is False
    return result['annotations']


@pytest.mark.parametrize('text,kind,status', [
    ('12h MA10 confirmed(82228.57로 호스트 자료상 아래 가격)', 'EXPLICIT_NUMERIC_CITATION', 'MISMATCH'),
    ('12h MA10 confirmed [[TF:1h:confirmed:close]]', 'REFERENCE_LABEL', 'MISMATCH'),
    ('확정 30m 종가는 82782.3에서 82751.7로 내려가며 MA 아래였다.', 'CONFIRMED_CLOSE_PAIR', 'UNASSESSED'),
    ('4h MA35 형성값 [[TF:4h:forming:ma35]] 부근은 현 가격 바로 위의 불확실한 반응 구간이다.', 'MA_MARK_POSITION', 'UNASSESSED'),
])
def test_four_incident_shapes(text, kind, status):
    rows = [a for a in annotations(text) if a['kind'] == kind]
    assert len(rows) == 1 and rows[0]['status'] == status


@pytest.mark.parametrize('text,kind', [
    ('최근 두 확정 30m 종가는 82782.3에서 82751.7로 내려갔다.', 'CONFIRMED_CLOSE_PAIR'),
    ('4h MA35 형성값 [[TF:4h:forming:ma35]]는 MarkPrice 바로 위의 구간이다.', 'MA_MARK_POSITION'),
])
def test_explicit_comparison_basis_allows_mechanical_mismatch(text, kind):
    assert next(a for a in annotations(text) if a['kind'] == kind)['status'] == 'MISMATCH'


def test_label_disagreement_does_not_invalidate_the_resolved_token_itself():
    rows = annotations('12h MA10 confirmed [[TF:1h:confirmed:close]]')
    assert next(a for a in rows if a['kind'] == 'TYPED_REFERENCE')['status'] == 'VERIFIED_REFERENCE'
    label = next(a for a in rows if a['kind'] == 'REFERENCE_LABEL')
    assert label['value'] == 82328.57 and label['field'] == 'ma10' and label['frame'] == '12h'


@pytest.mark.parametrize('text', [
    '12h MA10 confirmed(82328.57)',
    '12h MA10 confirmed [[TF:12h:confirmed:ma10]]',
    '확정 30m 종가는 82915.9에서 82782.3로 내려갔다.',
    '4h MA35 형성값 [[TF:4h:forming:ma35]] 부근은 현 가격 바로 아래의 구간이다.',
])
def test_correct_explicit_claims_are_not_mismatches(text):
    assert not any(a['status'] == 'MISMATCH' for a in annotations(text))


@pytest.mark.parametrize('text', [
    '이전 12h MA10 confirmed(82228.57)',
    '12h/1h MA10 confirmed(82228.57)',
    '과거 확정 30m 종가는 82782.3에서 82751.7로 내려갔다.',
    '확정 30m 종가는 82782.3에서 82751.7로 내려갔다가 현재 82900으로 반등했다.',
    '확정 30m 종가는 82782.3에서 82751.7로 내려갔더라도 이번 판단과 다르다.',
    '가정: 최근 두 확정 30m 종가는 100에서 90로 내려갔다.',
    '최근 두 확정 30m 종가는 100에서 90로 내려가야 한다.',
    '확정 30m 종가는 82782.3에서 82751.7로 내려갈 경우를 본다.',
    '12h MA10 confirmed. [[TF:1h:confirmed:close]]',
    '4h MA35 형성값 [[TF:4h:forming:ma35]]는 진입가 바로 위다.',
    '4h MA35 형성값 [[TF:4h:forming:ma35]]는 현 가격 위가 아니다.',
    '4h MA35 형성값 [[TF:4h:forming:ma35]]가 현 가격 위로 오르면 재검토한다.',
    '4h MA35 형성값 [[TF:4h:forming:ma35]]. 현 가격 바로 위에는 별도 저항이 있다.',
    '4h MA35 형성값 [[TF:4h:forming:ma35]] 및 1h MA10은 현 가격 바로 위다.',
])
def test_ambiguous_historical_conditional_or_other_basis_is_not_flagged(text):
    assert not any(a['status'] == 'MISMATCH' for a in annotations(text))


@pytest.mark.parametrize('fault', ['duplicate', 'gap', 'unconfirmed'])
def test_two_close_pair_requires_consecutive_unique_confirmed_sources(fault):
    snap, context = inputs()
    bars = snap['timeframes']['30m']['recent_confirmed_bars']
    if fault == 'duplicate':
        bars.append(deepcopy(bars[0]))
    elif fault == 'gap':
        bars[0]['open_time_ms'] -= 1800000
    else:
        bars[0]['is_confirmed'] = False
    result = analyze_explanation(dict(rationale='확정 30m 종가는 82782.3에서 82751.7로 내려갔다.'), snap, context)
    rows = [a for a in result['annotations'] if a['kind'] == 'CONFIRMED_CLOSE_PAIR']
    assert rows and rows[0]['status'] == 'UNVERIFIED'
