"""One entry policy: both confirmed primary MA10/35 gaps must be narrowing."""
import math


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('invalid_convergence_number')
    return value


def entry_convergence(snapshot):
    result = dict(version=1, status='UNKNOWN', frames={}, basis='CONFIRMED_ABSOLUTE_MA10_MA35_GAP')
    try:
        at = _number(snapshot['as_of_ms'])
        if snapshot.get('valid') is not True:
            return result
        result['as_of_ms'] = at
        result['valid_until'] = (int(at // 1800000)+1)*1800
        for frame, duration in (('1h', 3600000), ('30m', 1800000)):
            data = snapshot['timeframes'][frame]
            path = data['ma_path']
            if data.get('status') != 'ok' or path.get('status') != 'available':
                return result
            points = path['confirmed_points'][-2:]
            if len(points) != 2:
                return result
            before, after = points
            for point in points:
                if point.get('is_confirmed') is not True:
                    return result
                if (_number(point['open_time_ms']) + duration != _number(point['close_time_ms'])
                        or point['close_time_ms'] > at):
                    return result
            if (after['close_time_ms'] != int(at // duration)*duration
                    or before['close_time_ms'] + duration != after['close_time_ms']):
                return result
            averages = [(_number(p['ma10']), _number(p['ma35'])) for p in points]
            if any(min(values) <= 0 for values in averages):
                return result
            gaps = [abs(a-b) for a,b in averages]
            change = _number(gaps[1] - gaps[0])
            state = 'NARROWING' if change < 0 else 'WIDENING' if change > 0 else 'UNCHANGED'
            if path['confirmed_gap']['state'] != state:
                return result
            result['frames'][frame] = dict(state=state, change_price=change,
                                           confirmed_close_ms=after['close_time_ms'])
        result['status'] = ('BLOCKED' if all(x['state'] == 'NARROWING' for x in result['frames'].values())
                            else 'ALLOWED')
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        pass
    return result


def require_entry_convergence(context):
    """Absence is legacy mode; enabled callers always supply an explicit fact."""
    if 'entry_convergence' not in context:
        return
    fact = context['entry_convergence']
    if (not isinstance(fact, dict) or type(fact.get('version')) is not int
            or fact['version'] != 1 or fact.get('status') not in {'ALLOWED', 'BLOCKED'}):
        raise ValueError('entry_convergence_unknown')
    if fact['status'] == 'BLOCKED':
        raise ValueError('entry_convergence_restricted')
    now = _number(context.get('now'))
    if not 0 <= now - _number(fact.get('as_of_ms'))/1000 <= 120 or now >= _number(fact.get('valid_until')):
        raise ValueError('entry_convergence_stale')
