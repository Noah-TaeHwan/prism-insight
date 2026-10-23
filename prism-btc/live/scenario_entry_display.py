"""Bounded copies of validated facts; price order is not an obstacle or signal."""
import hashlib
import json
import math


def _price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def project_entry_display_input(payload):
    """Preserve legacy history selection before adding this lossless-only view."""
    from live.scenario_model_input import project_model_input, restore_model_input
    from live.scenario_position_flow import project_position_flow_input, _MODEL_INPUT_LIMIT, _size
    projected, audit = project_position_flow_input(payload)
    if payload.get('contract_context', {}).get('scenario_id') is not None:
        return projected, audit
    logical = restore_model_input(projected)
    context = logical['contract_context']
    context['entry_display'] = flat_entry_display(context['ma_structure'], logical.get('calculated_candidates'))
    augmented, augmented_audit = project_model_input(logical, allow_gap_rescue=True)
    if _size(augmented) > _MODEL_INPUT_LIMIT and _size(projected) <= _MODEL_INPUT_LIMIT:
        return projected, audit
    return augmented, augmented_audit


def flat_entry_display(structure, bundle=None):
    """Caller supplies its freshly built ma_structure; never mutate a receipt."""
    tails = {}
    primary = structure.get('primary', {})
    for frame in ('15m', '30m', '1h'):
        points = primary.get(frame, {}).get('points', [])
        tails[frame] = [[p['as_of_ms'], p['close']] for p in points
                        if p.get('phase') == 'CONFIRMED' and p.get('is_confirmed') is True][-3:] or None
    result = dict(confirmed_close_tail=tails,
        meaning='tail=[CONFIRMED bar_end_ms,close], null=unavailable; price order is numeric, not time/fill order or support strength. E#/T#=effective_plan.entries/take_profits[index].price; M:id=ma_structure.levels[id].price.')
    if bundle is None:
        return result
    candidates = bundle.get('candidates')
    levels = structure.get('levels', {})
    if not isinstance(candidates, list) or len(candidates) > 2 or not isinstance(levels, dict) or len(levels) > 16:
        result['candidate_price_order_status'] = 'UNAVAILABLE_BOUND'
        return result
    result['ma_levels_status'] = 'AVAILABLE' if levels else 'UNAVAILABLE'
    result['candidate_price_order'] = []
    for candidate in candidates:
        plan = candidate.get('effective_plan', {})
        if candidate.get('eligible') is not True or plan.get('action') != 'OPEN':
            continue
        record = [candidate.get('id'), candidate.get('receipt_sha256'), 'UNAVAILABLE', None]
        result['candidate_price_order'].append(record)
        body = {k: v for k, v in candidate.items() if k != 'receipt_sha256'}
        try:
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        except (TypeError, ValueError):
            continue
        entries, targets = plan.get('entries'), plan.get('take_profits')
        if (digest != record[1] or not isinstance(entries, list) or not 1 <= len(entries) <= 20
                or not isinstance(targets, list) or len(targets) > 20):
            continue
        values = [(row.get('price'), f'E{i}') for i, row in enumerate(entries)]
        values += [(row.get('price'), f'T{i}') for i, row in enumerate(targets)]
        values += [(row.get('price'), 'M:' + key) for key, row in levels.items()]
        if any(not _price(value) for value, _ in values):
            continue
        grouped = {}
        for value, ref in values:
            grouped.setdefault(value, []).append(ref)
        record[2:] = ['AVAILABLE', [[value, grouped[value]] for value in sorted(grouped)]]
    records = result['candidate_price_order']
    result['candidate_price_order_status'] = ('NO_ELIGIBLE_OPEN' if not records else
        'AVAILABLE' if all(row[2] == 'AVAILABLE' for row in records) else 'PARTIAL_UNAVAILABLE')
    return result
