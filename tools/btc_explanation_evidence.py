"""Read-only explanation views outside every trading/replay boundary.

Consume retained audit details, never a live database or trading entry point.
Original model text is not a verified fact; canonical cards come from its frozen
request. Failure produces an unverified sidecar, never a trading verdict.
"""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'prism-btc'))
from live.scenario_explanation_evidence import analyze_explanation  # noqa: E402
from live.scenario_model_input import restore_model_input  # noqa: E402

MAX_EVENTS = 104


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def view_event(event, slot, index, *, analyzer=analyze_explanation):
    view = dict(slot=slot, phase=event.get('phase'), event_index=index,
        source_ref=f'events[{slot}][{index}]', status='UNVERIFIED',
        raw_model_text_verified=False, execution_authorized=False,
        source_binding='UNVERIFIED', official_facts=[])
    try:
        request, raw = event['actual_request'], event['raw']
        if not isinstance(raw, str) or not isinstance(request, dict):
            raise ValueError('invalid_source')
        view['response_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        view['request_sha256'] = digest(request)
        if view['request_sha256'] != event.get('actual_request_sha256'):
            raise ValueError('request_binding_mismatch_or_missing')
        payload = restore_model_input(json.loads(request['user_prompt'], object_pairs_hook=unique_object))
        snapshot, context = payload['market_snapshot'], payload['contract_context']
        parsed = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(parsed, dict):
            raise ValueError('nonobject_response')
        evidence = analyzer(parsed, snapshot, context)
        # Check serializability before presenting derived metadata as a usable view.
        digest(evidence)
        view.update(source_binding='MATCHED_RETAINED_REQUEST', status='PARTIAL_SCOPE',
                    input_id=context.get('input_id'), evidence=evidence)
        cards = {}
        for row in evidence['annotations']:
            if 'canonical_fact' in row and 'source_ref' in row:
                key = (row['source_ref'], row['observed_at_ms'])
                cards[key] = {k: row[k] for k in ('frame', 'phase', 'field', 'value', 'open_time_ms',
                    'observed_at_ms', 'time_kind', 'snapshot_as_of_ms', 'source_ref', 'canonical_fact')}
        view['official_facts'] = list(cards.values())
    except Exception:
        # Do not expose exception text, invent source facts, or invoke any executor.
        view.update(status='UNVERIFIED', official_facts=[],
                    error='explanation_source_or_renderer_unavailable')
        view.pop('evidence', None)
    return view


def build_view(packet, *, analyzer=analyze_explanation):
    if not isinstance(packet, dict) or not isinstance(packet.get('events'), dict):
        raise ValueError('retained_details_events_required')
    views, total = [], 0
    for slot, events in packet['events'].items():
        if not isinstance(events, list):
            continue
        for index, event in enumerate(events):
            if not isinstance(event, dict) or event.get('kind') != 'MODEL_RAW':
                continue
            total += 1
            if len(views) < MAX_EVENTS:
                views.append(view_event(event, str(slot), index, analyzer=analyzer))
    statuses = Counter(a['status'] for v in views for a in v.get('evidence', {}).get('annotations', []))
    return dict(version=1, scope='DISPLAY_AND_AUDIT_ONLY_NOT_TRADING_OR_MODEL_REASONING_PROOF',
        original_text_policy='PRESERVED_IN_SOURCE_ALWAYS_UNVERIFIED_AS_WHOLE',
        source_events=total, examined_events=len(views), omitted_events=total-len(views),
        unverified_events=sum(v['status']=='UNVERIFIED' for v in views),
        annotation_counts=dict(statuses), views=views, execution_authorized=False)


def render_markdown(result):
    """Official number/phase text is emitted by code, not copied by a summarizer."""
    lines = ['# BTC 설명 수치 검증', '',
        f"원응답 {result['source_events']}건 중 {result['examined_events']}건 검사, "
        f"{result['omitted_events']}건 미검사입니다.",
        '모델 원문 전체와 판단 정확성을 검증한 결과가 아닙니다. 주문·보호 권한은 없습니다.', '']
    shown = 0
    for view in result['views']:
        annotations = view.get('evidence', {}).get('annotations', [])
        selected = [a for a in annotations if a.get('status') in ('MISMATCH', 'UNVERIFIED', 'UNASSESSED', 'VERIFIED_REFERENCE')]
        if not selected and view['status'] != 'UNVERIFIED':
            continue
        shown += 1
        lines.extend([f"## 슬롯 {view['slot']} · {view['phase']}", ''])
        if view['status'] == 'UNVERIFIED':
            lines.append('- 입력 연결 또는 표시 검증이 불가능합니다. 원문은 미검증입니다.')
        for annotation in selected:
            if annotation['status'] == 'MISMATCH':
                if annotation.get('kind') == 'REFERENCE_LABEL':
                    lines.append('- 명시 라벨·참조 불일치: 라벨 '+':'.join(annotation['label_reference'])
                                 +', 토큰 '+':'.join(annotation['token_reference'])+'.')
                elif annotation.get('kind') == 'MA_MARK_POSITION':
                    lines.append(f"- MA 위치 불일치: 설명은 {annotation['cited_position']}, "
                                 f"원본 MA {annotation['value']}는 Mark {annotation['comparison_value']}의 "
                                 f"{annotation['actual_position']}입니다.")
                else:
                    lines.append(f"- 수치 인용 불일치: 인용값 {annotation.get('cited_value')}, "
                                 f"원본 현재값 {annotation.get('value')}.")
                if 'actual_before' in annotation:
                    lines.append(f"- 기준값 대조: 인용 {annotation.get('cited_before')}, 원본 {annotation['actual_before']}.")
            elif annotation['status'] == 'UNVERIFIED':
                lines.append('- 참조 또는 인용 일부를 검증하지 못했습니다.')
            elif annotation['status'] == 'UNASSESSED':
                if annotation.get('kind') == 'CONFIRMED_CLOSE_PAIR':
                    lines.append(f"- 수동 대조 필요: 인용 구간이 최신 두 봉인지 불명확합니다. "
                                 f"인용 {annotation['cited_before']}→{annotation['cited_value']}, "
                                 f"최신 두 봉 {annotation['actual_before']}→{annotation['value']}.")
                elif annotation.get('kind') == 'MA_MARK_POSITION':
                    lines.append(f"- 수동 대조 필요: 현재가가 Mark인지 불명확합니다. MA {annotation['value']}, "
                                 f"참고 Mark {annotation['comparison_value']}이며 원문 위치 판정은 미평가입니다.")
            if 'canonical_fact' in annotation:
                lines.append('- '+annotation['canonical_fact'])
        lines.append('')
    if not shown:
        lines.append('좁은 검사 범위에서 표시할 불일치·참조가 없습니다. 전체 설명 정상 판정은 아닙니다.')
    lines.append('원문·계획·주문은 수정하지 않았습니다. 원문은 입력 상세 JSON에, 연결 해시와 검사 결과는 별도 JSON에 보존됩니다.')
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--details', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown-output', type=Path)
    args = parser.parse_args()
    if args.output.exists() or (args.markdown_output and args.markdown_output.exists()):
        raise FileExistsError('Use new output paths; never overwrite previous audit evidence')
    if args.markdown_output and args.output.resolve() == args.markdown_output.resolve():
        raise ValueError('JSON and Markdown output paths must differ')
    raw = args.details.read_bytes()
    result = build_view(json.loads(raw))
    result.update(source_path=str(args.details), source_sha256=hashlib.sha256(raw).hexdigest())
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
    if args.markdown_output:
        with args.markdown_output.open('x', encoding='utf-8') as stream:
            stream.write(render_markdown(result))
    print(json.dumps({k: v for k, v in result.items() if k != 'views'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
