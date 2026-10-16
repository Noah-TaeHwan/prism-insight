"""Optional historical-fee illustrations, never risk or execution authority."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math


def _d(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_number')
    result=Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def observed_fee_evidence(evidence, accounting, *, now):
    """Called only after complete owned-execution/transaction reconciliation.

    Metadata failure is optional: never invalidate settlement or protection.
    Missing maker identity is not inferred; rebates/zero rates are not estimates.
    """
    unknown=dict(status='UNKNOWN', reason='no_usable_verified_execution_fees')
    try:
        if accounting.get('status')!='confirmed' or accounting.get('accounting_complete') is not True:
            return unknown
        current=_d(now)
        groups={}
        sources=[]
        for trade in evidence.get('trades', []):
            raw,txn=trade['raw_execution'],trade['raw_transaction']
            maker=raw.get('isMaker')
            if type(maker) is not bool:
                continue
            qty,price,fee,stamp=map(_d,(raw.get('execQty'),raw.get('execPrice'),raw.get('execFee'),raw.get('execTime')))
            if (raw.get('symbol')!='BTCUSDT' or txn.get('symbol')!='BTCUSDT' or
                    raw.get('execType')!='Trade' or txn.get('type')!='TRADE' or
                    txn.get('currency')!='USDT' or not raw.get('execId') or
                    raw.get('execId')!=txn.get('tradeId') or raw.get('orderId')!=txn.get('orderId') or
                    fee!=_d(txn.get('fee')) or min(qty,price,fee,stamp)<=0 or
                    not 0<=current-stamp/1000<=86400):
                continue
            rate=fee/(qty*price)
            if not 0<rate<=Decimal('.01'):
                continue
            kind='MAKER' if maker else 'TAKER'
            groups.setdefault(kind,[]).append((rate,stamp/1000))
            sources.append((raw['execId'],raw['orderId'],str(qty),str(price),str(fee),str(stamp),maker))
        if not groups:
            return unknown
        return dict(status='AVAILABLE',basis='HISTORICAL_VERIFIED_EXECUTIONS',
            current_fee_tier_verified=False, future_rate_guaranteed=False,
            rates={kind:dict(count=len(rows),minimum=float(min(r for r,_ in rows)),
                maximum=float(max(r for r,_ in rows)),earliest_at=float(min(t for _,t in rows)),
                latest_at=float(max(t for _,t in rows)))
                for kind,rows in groups.items()},
            source_sha256=hashlib.sha256(json.dumps(sorted(sources),separators=(',',':')).encode()).hexdigest())
    except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
        return unknown


def fee_only_projection(*, side, entry_price, exit_price, quantity, evidence, now,
                        new_entry, recorded_net=None):
    """TAKER historical upper observed rate illustration, not net-profit guarantee.

    A new leg includes its attributable entry fee. Held lots have paid entry fees
    in recorded accounting already. Only a full remaining exit may pass recorded_net.
    Unknown future funding/slippage are excluded, never filled with zero.
    """
    unknown=dict(status='UNKNOWN',reason='missing_or_stale_taker_fee_evidence')
    try:
        if not isinstance(evidence,dict) or evidence.get('status')!='AVAILABLE' or evidence.get('basis')!='HISTORICAL_VERIFIED_EXECUTIONS':
            return unknown
        source=evidence.get('source_sha256')
        if not isinstance(source,str) or len(source)!=64 or any(c not in '0123456789abcdef' for c in source):
            return unknown
        row=evidence.get('rates',{}).get('TAKER',{})
        rate,minimum,stamp,current=map(_d,(row.get('maximum'),row.get('minimum'),row.get('latest_at'),now))
        earliest=_d(row.get('earliest_at'))
        if (type(row.get('count')) is not int or row['count']<=0 or
                not 0<minimum<=rate<=Decimal('.01') or not 0<earliest<=stamp<=current or
                not 0<=current-earliest<=86400):
            return unknown
        entry,exit_,qty=map(_d,(entry_price,exit_price,quantity))
        if side not in ('LONG','SHORT') or min(entry,exit_,qty)<=0 or type(new_entry) is not bool:
            return dict(status='UNKNOWN',reason='invalid_projection_inputs')
        gross=(exit_-entry)*qty*(1 if side=='LONG' else -1)
        entry_fee=entry*qty*rate if new_entry else Decimal(0)
        exit_fee=exit_*qty*rate
        recorded=_d(recorded_net) if recorded_net is not None else Decimal(0)
        if new_entry and recorded_net is not None:
            return dict(status='UNKNOWN',reason='mixed_accounting_scope')
        values=dict(estimated_gross_pnl=gross,assumed_entry_fee=entry_fee,
            assumed_exit_fee=exit_fee,estimated_net_before_unmodelled_costs=recorded+gross-entry_fee-exit_fee)
        values={key:float(value) for key,value in values.items()}
        if not all(math.isfinite(value) for value in values.values()):
            return dict(status='UNKNOWN',reason='nonfinite_projection')
        return dict(status='AVAILABLE',scope='WHOLE_SCENARIO' if recorded_net is not None else 'LEG_ONLY',
            entry_fee_treatment='ATTRIBUTABLE_NEW_ENTRY' if new_entry else 'ALREADY_RECORDED_NOT_CHARGED_AGAIN',
            basis='HISTORICAL_TAKER_MAX_ASSUMPTION_NOT_CURRENT_TIER',
            assumed_fee_rate=float(rate),future_slippage='UNKNOWN_EXCLUDED',future_funding='UNKNOWN_EXCLUDED',
            estimates_not_guaranteed=True,**values)
    except (AttributeError,KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
        return unknown
