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
