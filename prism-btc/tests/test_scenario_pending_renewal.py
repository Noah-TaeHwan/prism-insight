"""Explicit lease permission only; no exchange network or production databases."""
from copy import deepcopy

import pytest

from core.scenario_pending_renewal import compile_renewal, lease_deadline


def renewal_context(now=1300):
    return dict(now=now, scenario_id='s', positions=[],
        pending_entries=[dict(id='e', price=101, quantity=.1, trigger_price=101,
                              order_status='Untriggered', expires_at=1320)],
        initial_equity=10000, previous_hard_stop=98, side='LONG', mark_price=100,
        realized_loss=0, fees_paid=0, funding_paid=0, estimated_cost_rate=.002, slippage_bps=20,
        accounting_status='confirmed', protection_ok=True, legacy_fenced=False, new_risk_blocked=False,
        pending_renewal_version=1, pending_renewal_offer=dict(offer_id='offer',
            original_action_id='open', lease_revision=0, from_expires_at=1320,
            to_expires_at=1620, hard_deadline=4600, slot=4, contract_id='contract', risk_budget=200),
        entry_contract_status='REGISTERED', registered_entry_contract=dict(contract_id='contract'))


def test_deadline_is_next_slot_plus_120_bounded_by_original_hour():
    assert lease_deadline(1000, 1000) == 1320
    assert lease_deadline(1300, 1000) == 1620
    assert lease_deadline(4500, 1000) == 4600
    assert lease_deadline(4600, 1000) == 4600


def test_explicit_approval_compiles_host_offer_without_changing_input():
    ctx = renewal_context()
    before = deepcopy(ctx)
    grant, risk = compile_renewal(dict(offer_id='offer'), ctx)
    assert grant['to_expires_at'] == 1620
    assert grant['contract_id'] == 'contract'
    assert risk['within_budget'] is True and risk['proposed_risk'] == 0
    assert ctx == before


@pytest.mark.parametrize('fault', ['expired', 'wrong_offer', 'filled', 'triggered', 'unknown', 'failed', 'risk'])
def test_renewal_rejects_invalid_states(fault):
    ctx = renewal_context()
    approval = dict(offer_id='offer')
    if fault == 'expired': ctx['now'] = 1320
    if fault == 'wrong_offer': approval['offer_id'] = 'other'
    if fault == 'filled': ctx['positions'] = [dict(price=101, quantity=.01)]
    if fault == 'triggered': ctx['pending_entries'][0]['order_status'] = 'New'
    if fault == 'unknown': ctx['accounting_status'] = 'pending'
    if fault == 'failed': ctx['entry_contract_observation'] = dict(status='FAILED')
    if fault == 'risk': ctx['pending_entries'][0]['quantity'] = 10000
    with pytest.raises(ValueError): compile_renewal(approval, ctx)


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'missing'])
def test_unknown_hard_flags_never_authorize_wait(value):
    ctx = renewal_context()
    if value == 'missing': ctx.pop('new_risk_blocked')
    else: ctx['new_risk_blocked'] = value
    with pytest.raises(ValueError): compile_renewal(dict(offer_id='offer'), ctx)


def test_soft_block_is_not_confused_with_explicit_clear_hard_guard():
    ctx = renewal_context()
    ctx.update(new_risk_blocked=True, recovery_hard_blocked=False)
    assert compile_renewal(dict(offer_id='offer'), ctx)[1]['within_budget']
    ctx['recovery_hard_blocked'] = True
    with pytest.raises(ValueError): compile_renewal(dict(offer_id='offer'), ctx)


def test_offline_replay_hash_covers_both_new_tables_without_ignoring_unknown_tables():
    import sqlite3
    from analysis.scenario_replay import _runtime_event_hash
    from backtest.scenario_tape import TapeMismatch
    from live.scenario_pending_renewal import initialize
    conn = sqlite3.connect(':memory:')
    initialize(conn)
    empty = _runtime_event_hash(conn)
    conn.execute('INSERT INTO llm_scenario_pending_leases VALUES(?,?)', ('s', '{"revision":0}'))
    anchored = _runtime_event_hash(conn)
    assert anchored != empty
    conn.execute('INSERT INTO llm_scenario_pending_renewals VALUES(?,?)', ('renew', '{"status":"APPLIED"}'))
    assert _runtime_event_hash(conn) != anchored
    conn.execute('CREATE TABLE llm_scenario_unregistered(value TEXT)')
    with pytest.raises(TapeMismatch, match='unknown_runtime_event_table'):
        _runtime_event_hash(conn)
    conn.close()
