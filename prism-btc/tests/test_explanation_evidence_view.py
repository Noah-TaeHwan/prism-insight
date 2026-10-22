"""The actual report consumer remains entirely outside trading authority."""
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.btc_explanation_evidence import build_view, digest, render_markdown, view_event  # noqa: E402


def event():
    request = dict(user_prompt=json.dumps(dict(market_snapshot={}, contract_context={})))
    return dict(kind='MODEL_RAW', phase='candidate', actual_request=request,
        actual_request_sha256=digest(request), raw=json.dumps(dict(
            wait_proposal=dict(rationale='original WAIT'), candidates=[], no_candidate_reason='no change')))


def test_local_label_and_position_mismatches_render_their_actual_basis():
    result = dict(source_events=1, examined_events=1, omitted_events=0,
        views=[dict(slot='1', phase='candidate', status='PARTIAL_SCOPE', evidence=dict(annotations=[
        dict(kind='REFERENCE_LABEL', status='MISMATCH', label_reference=['12h', 'confirmed', 'ma10'],
             token_reference=['1h', 'confirmed', 'close']),
        dict(kind='MA_MARK_POSITION', status='MISMATCH', cited_position='위', value=82197.9171,
             comparison_value=82200.23, actual_position='아래'),
    ]))])
    rendered = render_markdown(result)
    assert '라벨 12h:confirmed:ma10, 토큰 1h:confirmed:close' in rendered
    assert 'MA 82197.9171는 Mark 82200.23의 아래' in rendered
    assert 'None' not in rendered


def test_ambiguous_basis_is_displayed_as_unassessed_not_numeric_error():
    result = dict(source_events=1, examined_events=1, omitted_events=0,
        views=[dict(slot='1', phase='candidate', status='PARTIAL_SCOPE', evidence=dict(annotations=[
            dict(kind='CONFIRMED_CLOSE_PAIR', status='UNASSESSED', cited_before=82782.3,
                 cited_value=82751.7, actual_before=82915.9, value=82782.3),
            dict(kind='MA_MARK_POSITION', status='UNASSESSED', value=82197.9171,
                 comparison_value=82200.23),
        ]))])
    text = render_markdown(result)
    assert text.count('수동 대조 필요') == 2 and '수치 인용 불일치' not in text
    assert '인용 82782.3→82751.7, 최신 두 봉 82915.9→82782.3' in text


def test_metadata_failure_does_not_mutate_original_event_or_grant_authority():
    source = event()
    before = deepcopy(source)
    def fail(*args):
        raise RuntimeError('not a trading error')
    result = view_event(source, '1', 0, analyzer=fail)
    assert source == before
    assert result['status'] == 'UNVERIFIED' and result['official_facts'] == []
    assert result['execution_authorized'] is False


def test_request_binding_mismatch_never_renders_official_values():
    source = event()
    source['actual_request']['user_prompt'] += ' '
    result = view_event(source, '1', 0)
    assert result['status'] == 'UNVERIFIED' and not result['official_facts']


def test_single_draft_selection_and_no_selection_wait_share_consumer():
    sources = [event() for _ in range(3)]
    for e, phase in zip(sources, ('single', 'candidate', 'selection')):
        e['phase'] = phase
    seen = []
    def check(parsed, snapshot, context):
        seen.append(parsed)
        return dict(annotations=[])
    result = build_view(dict(events={'1': sources}), analyzer=check)
    assert result['examined_events'] == 3
    assert all(v['status'] == 'PARTIAL_SCOPE' for v in result['views'])
    assert all(p['no_candidate_reason'] == 'no change' for p in seen)
    assert all(not v['raw_model_text_verified'] for v in result['views'])


def test_duplicate_json_and_missing_source_are_unverified():
    source = event()
    source['raw'] = '{"rationale":"a","rationale":"b"}'
    assert view_event(source, '1', 0)['status'] == 'UNVERIFIED'
    source.pop('actual_request')
    assert view_event(source, '1', 0)['status'] == 'UNVERIFIED'


def test_bounded_events_remain_omitted_not_passed():
    result = build_view(dict(events={'1': [event() for _ in range(105)]}))
    assert result['examined_events'] == 104 and result['omitted_events'] == 1
    assert result['execution_authorized'] is False


def test_display_uses_host_fact_without_certifying_original_model_text():
    annotation = dict(status='MISMATCH', cited_value=84668.9, value=84549.9,
        canonical_fact='호스트 원본 사실: 30m confirmed close=84549.9')
    result = dict(source_events=1, examined_events=1, omitted_events=0,
        views=[dict(slot='5975320', phase='candidate', status='PARTIAL_SCOPE',
                    evidence=dict(annotations=[annotation]))])
    text = render_markdown(result)
    assert '인용값 84668.9, 원본 현재값 84549.9' in text
    assert annotation['canonical_fact'] in text
    assert '모델 원문 전체와 판단 정확성을 검증한 결과가 아닙니다' in text
