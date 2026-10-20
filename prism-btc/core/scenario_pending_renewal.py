"""Version-one pending lease arithmetic; never submission or exchange authority."""
from copy import deepcopy
import hashlib
import json
import math


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def lease_deadline(now, original_created_at):
    if not finite(now) or not finite(original_created_at) or now < original_created_at:
        raise ValueError('pending_renewal_invalid_clock')
    return min((int(now // 300) + 1) * 300 + 120, original_created_at + 3600)


def compile_renewal(approval, context):
    """Compile the exact host offer, checking current unchanged reserved risk."""
    from core.llm_scenario import risk_snapshot
    offer, now = context.get('pending_renewal_offer'), context.get('now')
    if (context.get('pending_renewal_version') != 1 or not isinstance(offer, dict)
            or not isinstance(approval, dict) or set(approval) != {'offer_id'}
            or approval['offer_id'] != offer.get('offer_id') or not finite(now)
            or not all(finite(offer.get(k)) for k in ('from_expires_at', 'to_expires_at', 'hard_deadline'))
            or not now < offer['from_expires_at'] < offer['to_expires_at'] <= offer['hard_deadline']
            or offer.get('slot') != int(now // 300)
            or offer['to_expires_at'] > (int(now // 300) + 1) * 300 + 120):
        raise ValueError('pending_renewal_offer_invalid')
    if (not context.get('scenario_id') or context.get('positions') != []
            or context.get('accounting_status') != 'confirmed'
            or context.get('protection_ok') is not True or context.get('legacy_fenced') is not False
            or type(context.get('new_risk_blocked')) is not bool
            or ('recovery_hard_blocked' in context and type(context['recovery_hard_blocked']) is not bool)
            or context.get('recovery_hard_blocked', context.get('new_risk_blocked')) is not False
            or context.get('entry_contract_status') != 'REGISTERED'
            or (context.get('registered_entry_contract') or {}).get('contract_id') != offer.get('contract_id')
            or (context.get('entry_contract_observation') or {}).get('status') == 'FAILED'):
        raise ValueError('pending_renewal_unsafe_context')
    rows = context.get('pending_entries')
    side, stop, mark = context.get('side'), context.get('previous_hard_stop'), context.get('mark_price')
    if (not isinstance(rows, list) or not rows or side not in ('LONG', 'SHORT')
            or not finite(stop) or not finite(mark) or min(stop, mark) <= 0):
        raise ValueError('pending_renewal_missing_reservation')
    for row in rows:
        trigger, price = row.get('trigger_price'), row.get('price')
        if (row.get('order_status') != 'Untriggered' or not finite(trigger) or not finite(price)
                or row.get('expires_at') != offer['from_expires_at']
                or (side == 'LONG' and not stop < mark < trigger <= price)
                or (side == 'SHORT' and not stop > mark > trigger >= price)):
            raise ValueError('pending_renewal_reservation_changed')
    risk = risk_snapshot(initial_equity=context['initial_equity'], side=side, hard_stop=stop,
        risk_fraction=context.get('scenario_risk_fraction', .02), positions=[], pending_entries=rows,
        entries=[], **{k: context[k] for k in ('realized_loss', 'fees_paid', 'funding_paid',
                                            'estimated_cost_rate', 'slippage_bps')})
    if not risk['within_budget'] or risk['budget'] != offer.get('risk_budget'):
        raise ValueError('pending_renewal_risk_exceeded')
    return deepcopy(offer), risk
