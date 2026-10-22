"""Non-authorizing explanation evidence from one frozen input; never rewrites text."""
from decimal import Decimal
import hashlib
import json
import math
import re


FRAMES = {'15m': 900000, '30m': 1800000, '1h': 3600000, '4h': 14400000,
          '12h': 43200000, '1d': 86400000, '1w': 604800000}
PROSE_KEYS = {'rationale', 'reason', 'counterevidence', 'invalidation', 'premise', 'no_candidate_reason'}
MAX_ANNOTATIONS, MAX_FIELDS, MAX_CHARS, MAX_NODES = 32, 32, 12000, 256
NUMBER = r'(?P<{name}>[+-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)'
VALUE = NUMBER.format(name='first') + r'(?:\s*(?:→|->)\s*' + NUMBER.format(name='second') + r')?(?!\d|[.,]\d|[eE][+-]?\d|[만천])(?!\s*(?:→|->))'
FULL = re.compile(r'(?<![\w.])(?P<ref>timeframes\.(?P<frame>15m|30m|1h|4h|12h|1d|1w)\.'
    r'(?P<phase>confirmed|forming)\.(?P<field>ohlcv\.close|ma10|ma35))`?\s*(?:는|은|도|:|=)?\s*' + VALUE)
SINGLE = re.compile(r'(?<![\w./·,])(?P<frame>15m|30m|1h|4h|12h|1d|1w)\s+'
    r'(?P<phase>confirmed|forming|확정|형성)\s+(?P<field>종가|close|MA10|MA35)\s*(?:는|은|:|=)?\s*' + VALUE)
TOKEN = re.compile(r'\[\[TF:(15m|30m|1h|4h|12h|1d|1w):(confirmed|forming):(close|ma10|ma35)\]\]')
LABEL = re.compile(r'(?<![\w./·,])(?P<frame>15m|30m|1h|4h|12h|1d|1w)\s+'
    r'(?P<field>MA10|MA35|종가|close)\s+(?P<phase>confirmed|forming|확정|형성)(?:값)?(?![A-Za-z])')
LOCAL_NUMBER = re.compile(r'\s*\(?\s*' + NUMBER.format(name='value') + r'(?!\d|[.,]\d|[eE][+-]?\d|[만천])')
CLOSE_PAIR = re.compile(r'(?<![\w./·,])확정\s*(?P<frame>15m|30m|1h|4h|12h|1d|1w)\s+종가는?\s*'
    + NUMBER.format(name='before') + r'에서\s*' + NUMBER.format(name='after') + r'로\s*내려(?:가|갔)')
POSITION = re.compile(r'\s*(?:부근은|는|은)?\s*(?P<basis>현 가격|현재가|현재 가격|MarkPrice|MARK_PRICE)\s*'
    r'(?:바로\s*)?(?P<position>위|아래)(?:의|에|다|이다)(?!\s*(?:아니|없))')


def _ambiguous_prefix(text, start):
    prefix = text[max(0, start-24):start]
    return bool(re.search(r'직전|이전|과거|기준선|baseline|예상|미래|가정|만약|(?:15m|30m|1h|4h|12h|1d|1w)\s*(?:·|/|,|및|and)\s*$', prefix, re.I))


def _local_claims(text, snapshot, context, path):
    """Only adjacent, explicit labels/clauses; never infer arbitrary prose semantics."""
    rows = []
    for match in LABEL.finditer(text):
        if _ambiguous_prefix(text, match.start()):
            continue
        frame, phase, field = match['frame'], match['phase'], match['field']
        phase = {'확정': 'confirmed', '형성': 'forming'}.get(phase, phase)
        field = {'종가': 'close'}.get(field, field.lower())
        tail = text[match.end():]
        token = re.match(r'\s*(' + TOKEN.pattern + ')', tail)
        if token:
            reference = TOKEN.fullmatch(token[1])
            fact = _resolve(snapshot, frame, phase, field)
            if fact['status'] == 'RESOLVED':
                fact['status'] = 'MATCH' if reference.groups() == (frame, phase, field) else 'MISMATCH'
            rows.append(dict(kind='REFERENCE_LABEL', prose_path=path, span=[match.start(), match.end()+token.end()],
                raw_excerpt=match[0]+token[0], label_reference=[frame, phase, field],
                token_reference=list(reference.groups()), **fact))
            # Only the matching MA label and its immediate same-clause position.
            position = POSITION.match(tail[token.end():])
            if fact['status'] == 'MATCH' and field in ('ma10', 'ma35') and position:
                mark = context.get('mark_price') if isinstance(context, dict) else None
                relation = dict(fact)
                relation.update(status='UNVERIFIED', comparison_source_ref='context.mark_price', cited_position=position['position'])
                if _number(mark) and mark > 0:
                    actual = '위' if fact['value'] > mark else '아래' if fact['value'] < mark else '동일'
                    relation.update(status='MATCH' if actual == position['position'] else 'MISMATCH',
                                    comparison_value=mark, actual_position=actual)
                    if position['basis'] not in ('MarkPrice', 'MARK_PRICE'):
                        relation.update(status='UNASSESSED', reason='price_basis_not_explicit_mark')
                rows.append(dict(kind='MA_MARK_POSITION', prose_path=path,
                    span=[match.start(), match.end()+token.end()+position.end()],
                    raw_excerpt=match[0]+token[0]+position[0], **relation))
        else:
            number = LOCAL_NUMBER.match(tail)
            if not number or re.match(r'\s*(?:에서|부터|→|->|~|[-–]\s*\d)', tail[number.end():]):
                continue
            cited = number['value']
            if len(cited.replace(',', '')) > 32:
                continue
            fact = _resolve(snapshot, frame, phase, field)
            fact['cited_value'] = float(cited.replace(',', ''))
            if fact['status'] == 'RESOLVED':
                fact['status'] = 'MATCH' if _rounded_match(cited, fact['value']) else 'MISMATCH'
            rows.append(dict(kind='EXPLICIT_NUMERIC_CITATION', prose_path=path,
                span=[match.start(), match.end()+number.end()], raw_excerpt=match[0]+number[0], **fact))
    for match in CLOSE_PAIR.finditer(text):
        latest_pair = bool(re.search(r'(?:최근|직전)\s*(?:두|2)\s*(?:개|봉)?\s*$', text[max(0, match.start()-16):match.start()]))
        if (_ambiguous_prefix(text, match.start()) and not latest_pair
                or re.search(r'가정|만약|예상|미래|과거|이전|기준선|baseline', text[max(0, match.start()-32):match.start()], re.I)
                or any(len(match[key].replace(',', '')) > 32 for key in ('before', 'after'))
                or re.match(r'(?:면|거나|는\s*경우|ㄹ|다가|더라도|야|려|겠)', text[match.end():])):
            continue
        fact = _resolve(snapshot, match['frame'], 'confirmed', 'close')
        fact.update(cited_before=float(match['before'].replace(',', '')),
                    cited_value=float(match['after'].replace(',', '')))
        if fact['status'] == 'RESOLVED':
            frame = snapshot['timeframes'][match['frame']]
            previous_start = fact['open_time_ms']-FRAMES[match['frame']]
            previous = [(i, b) for i, b in enumerate(frame['recent_confirmed_bars'])
                        if isinstance(b, dict) and b.get('open_time_ms') == previous_start]
            fact['status'] = 'UNVERIFIED'
            if (len(previous) == 1 and previous[0][1].get('is_confirmed') is not False
                    and previous[0][1].get('close_time_ms', fact['open_time_ms']) == fact['open_time_ms']
                    and _number(previous[0][1].get('close')) and previous[0][1]['close'] > 0):
                index, bar = previous[0]
                fact.update(actual_before=bar['close'], before_source_ref=f'timeframes.{match["frame"]}.recent_confirmed_bars[{index}].close')
                fact['status'] = 'MATCH' if (_rounded_match(match['before'], bar['close'])
                    and _rounded_match(match['after'], fact['value'])) else 'MISMATCH'
                if not latest_pair:
                    fact.update(status='UNASSESSED', reason='pair_not_explicitly_latest_two')
        rows.append(dict(kind='CONFIRMED_CLOSE_PAIR', prose_path=path, span=list(match.span()), raw_excerpt=match[0], **fact))
    return rows


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _resolve(snapshot, frame, phase, field):
    unavailable = dict(status='UNVERIFIED', reason='missing_invalid_or_future_source')
    if not isinstance(snapshot, dict) or snapshot.get('valid') is not True:
        return unavailable
    asof = snapshot.get('as_of_ms')
    frames = snapshot.get('timeframes')
    source = frames.get(frame) if isinstance(frames, dict) else None
    if (not _number(asof) or not isinstance(source, dict)
            or source.get('valid') is False
            or source.get('status') not in (None, 'available', 'ok', 'valid')):
        return unavailable
    point = source.get(phase)
    if not isinstance(point, dict) or point.get('valid') is False:
        return unavailable
    start = point.get('open_time_ms')
    duration = FRAMES[frame]
    if not _number(start) or start < 0:
        return unavailable
    source_path = f'timeframes.{frame}.{phase}.{field}'
    if phase == 'forming':
        stamp = point.get('observed_at_ms')
        if (point.get('is_confirmed') is not False or point.get('observation_kind') == 'synthetic_boundary'
                or not _number(stamp) or not start <= stamp < start+duration or stamp > asof):
            return unavailable
        if field == 'close':
            ohlcv = point.get('ohlcv')
            value = ohlcv.get('close') if isinstance(ohlcv, dict) else None
            source_path = f'timeframes.{frame}.forming.ohlcv.close'
        else:
            value = point.get(field)
    else:
        stamp = start+duration
        if (point.get('is_confirmed') is not True or stamp > asof
                or point.get('close_time_ms', stamp) != stamp):
            return unavailable
        value = point.get(field)
        if field == 'close':
            bars = source.get('recent_confirmed_bars')
            if not isinstance(bars, list):
                return unavailable
            eligible = [(i, b) for i, b in enumerate(bars) if isinstance(b, dict)
                and _number(b.get('open_time_ms')) and b['open_time_ms']+duration <= asof]
            if not eligible:
                return unavailable
            latest = max(b['open_time_ms'] for _, b in eligible)
            matches = [(i, b) for i, b in eligible if b['open_time_ms'] == latest]
            if latest != start or len(matches) != 1:
                return unavailable
            index, bar = matches[0]
            if bar.get('is_confirmed') is False or bar.get('close_time_ms', stamp) != stamp:
                return unavailable
            value = bar.get('close')
            source_path = f'timeframes.{frame}.recent_confirmed_bars[{index}].close'
    if not _number(value) or value <= 0:
        return unavailable
    time_kind = 'BAR_CLOSE' if phase == 'confirmed' else 'OBSERVATION'
    time_label = '봉 종료시각' if phase == 'confirmed' else '관측시각'
    return dict(status='RESOLVED', frame=frame, phase=phase, field=field, value=value,
        open_time_ms=start, observed_at_ms=stamp, source_ref=source_path,
        time_kind=time_kind, snapshot_as_of_ms=asof,
        canonical_fact=f'호스트 원본 사실: {frame} {phase} {field}={value} ({time_label} {stamp}ms)')


def _rounded_match(citation, value):
    number = citation.replace(',', '')
    decimals = len(number.split('.')[1]) if '.' in number else 0
    return abs(Decimal(number)-Decimal(str(value))) <= Decimal(5).scaleb(-decimals-1)


def analyze_explanation(parsed, snapshot, context):
    """Bounded allowlisted prose inspection; no confidence in overall free text."""
    result = dict(version=1, status='PARTIAL_SCOPE', authoritative_for_trading=False,
        free_prose_verified=False, snapshot_sha256=_hash(snapshot), context_sha256=_hash(context),
        annotations=[], scanned_prose_fields=0, unassessed_prose_count=0, truncated=False)
    annotations = result['annotations']
    nodes = chars = 0
    def inspect(text, path):
        nonlocal chars
        remaining = MAX_CHARS-chars
        if len(text) > remaining:
            # A prefix cut can turn an incomplete price or token into a false
            # complete citation. Omit the entire field, consuming the budget.
            chars = MAX_CHARS
            result['truncated'] = True
            result['unassessed_prose_count'] += 1
            return
        segment = text
        chars += len(segment)
        found = []
        for marker in re.finditer(r'\[\[TF:', segment):
            start = marker.start()
            end = segment.find(']]', start, start+128)
            end = min(start+128, len(segment)) if end < 0 else end+2
            token = segment[start:end]
            match = TOKEN.fullmatch(token)
            fact = _resolve(snapshot, *match.groups()) if match else dict(status='UNVERIFIED', reason='malformed_reference')
            if fact['status'] == 'RESOLVED':
                fact['status'] = 'VERIFIED_REFERENCE'
            found.append(dict(kind='TYPED_REFERENCE', prose_path=path, span=[start, end],
                              raw_excerpt=token, **fact))
        for expression in (FULL, SINGLE):
            for match in expression.finditer(segment):
                prefix, suffix = segment[max(0, match.start()-24):match.start()], segment[match.end():]
                if (expression is SINGLE and re.search(r'(?:15m|30m|1h|4h|12h|1d|1w)\s*(?:·|/|,|및|and)\s*$', prefix)
                        or re.match(r'\s*(?:~|–|-)\s*\d', suffix)
                        or re.match(r'\s*(?:에서|부터)', suffix)
                        or not match['second'] and re.search(r'(?:직전|이전|과거|기준선|baseline)', prefix, re.I)):
                    continue  # Never mistake an unsupported historical/from-to value for current.
                frame, phase, field = match['frame'], match['phase'], match['field']
                phase = {'확정': 'confirmed', '형성': 'forming'}.get(phase, phase)
                field = {'종가': 'close', 'ohlcv.close': 'close'}.get(field, field.lower())
                fact = _resolve(snapshot, frame, phase, field)
                cited = match['second'] or match['first']
                if any(len(v.replace(',', '')) > 32 for v in (match['first'], cited)):
                    found.append(dict(kind='EXPLICIT_NUMERIC_CITATION', prose_path=path,
                        span=list(match.span()), status='UNVERIFIED', reason='numeric_citation_too_long'))
                    continue
                fact['cited_value'] = float(cited.replace(',', ''))
                if fact['status'] == 'RESOLVED':
                    fact['status'] = 'MATCH' if _rounded_match(cited, fact['value']) else 'MISMATCH'
                if match['second']:
                    fact['cited_before'] = float(match['first'].replace(',', ''))
                    ref = f'timeframes.{frame}.{phase}.' + ('ohlcv.close' if field == 'close' else field)
                    # Only an explicit full path anchors the pair to the supplied
                    # recovery changes. A single-frame arrow can compare candles
                    # instead; its starting point remains deliberately unassessed.
                    recovery = context.get('recovery') if isinstance(context, dict) and expression is FULL else None
                    changes = recovery.get('changed_evidence') if isinstance(recovery, dict) else None
                    pair = changes.get(ref) if isinstance(changes, dict) else None
                    before = pair.get('before') if isinstance(pair, dict) else None
                    if _number(before) and before > 0:
                        fact['actual_before'] = before
                        fact['before_source_ref'] = 'context.recovery.changed_evidence.'+ref+'.before'
                        if not _rounded_match(match['first'], before):
                            fact['status'] = 'MISMATCH' if 'value' in fact else 'UNVERIFIED'
                    else:
                        fact['before_status'] = 'UNVERIFIED' if expression is FULL else 'UNASSESSED'
                        if fact['status'] == 'MATCH' and expression is FULL:
                            fact['status'] = 'UNVERIFIED'
                found.append(dict(kind='EXPLICIT_NUMERIC_CITATION', prose_path=path,
                    span=list(match.span()), raw_excerpt=match[0], **fact))
        found.extend(_local_claims(segment, snapshot, context, path))
        found.sort(key=lambda a: a['span'])
        capacity = MAX_ANNOTATIONS-len(annotations)
        annotations.extend(found[:capacity])
        if len(found) > capacity:
            result['truncated'] = True
        if not found:
            result['unassessed_prose_count'] += 1

    def walk(value, path='', depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > 12 or result['scanned_prose_fields'] >= MAX_FIELDS:
            result['truncated'] = True
            return
        if isinstance(value, dict):
            for index, (key, item) in enumerate(value.items()):
                if index >= MAX_NODES or nodes > MAX_NODES:
                    result['truncated'] = True
                    break
                where = f'{path}.{key}' if path else key
                if key in PROSE_KEYS and isinstance(item, str):
                    if result['scanned_prose_fields'] >= MAX_FIELDS:
                        result['truncated'] = True
                        break
                    result['scanned_prose_fields'] += 1
                    inspect(item, where)
                elif isinstance(item, (dict, list)):
                    walk(item, where, depth+1)
        elif isinstance(value, list):
            for index, item in enumerate(value[:MAX_NODES]):
                walk(item, f'{path}[{index}]', depth+1)
            if len(value) > MAX_NODES:
                result['truncated'] = True
    walk(parsed)
    return result
