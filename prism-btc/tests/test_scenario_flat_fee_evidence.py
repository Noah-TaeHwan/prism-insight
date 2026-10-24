"""Prospective same-account fee evidence, never fabricated flat accounting."""
import json
import sqlite3
from copy import deepcopy

import pytest

from live.scenario_broker import ScenarioDemoBroker
from live.scenario_calculated_review import calculate_candidate
from live.scenario_control import begin_transition
from tests.test_scenario_broker import Session, funding_capture_fixture
from tests.test_scenario_calculated_review import context, wire

KIND = 'review_fee_sidecar_v1'


def seeded(tmp_path):
    conn = sqlite3.connect(tmp_path / 'fee.db')
    session = Session()
    broker = ScenarioDemoBroker(conn, session=session, expected_main_uid='123', clock=lambda: .1)
    begin_transition(conn, '123', clock=lambda: .1)
    executions, _ = funding_capture_fixture(session)
    for row in executions:
        row['isMaker'] = False
    observed = dict(position=dict(symbol='BTCUSDT', positionIdx=0, size='0', side=''), open_orders=[])
    broker._funding_schedule = lambda *args: dict(start_ms=0, end_ms=args[1], complete=True,
                                                 events=[dict(timestamp=20, rate='.001')])
    children = [dict(order_id=oid, kind=kind, status='TERMINAL', request={'side': side},
                     evidence={'order': {'cumExecQty': '1'}})
        for oid, kind, side in [('entry-order', 'entry', 'Buy'), ('exit-order', 'tp', 'Sell')]]
    return broker, session, observed, children


def account(broker, observed, children):
    return broker._accounting(observed, dict(scenario_id='s', created_at=0), children)


def cache(broker):
    return broker.conn.execute('SELECT captured_at,body FROM llm_scenario_broker_evidence WHERE kind=?', (KIND,)).fetchall()


def test_real_accounting_restart_flat_context_to_candidate(tmp_path):
    broker, session, observed, children = seeded(tmp_path)
    result = account(broker, observed, children)
    assert result['accounting_complete'] is True
    assert len(cache(broker)) == 1
    saved = cache(broker)
    broker.clock = lambda: .2
    account(broker, observed, children)
    assert cache(broker) == saved
    fresh = ScenarioDemoBroker(broker.conn, session=session, expected_main_uid='123', clock=lambda: .2)
    fresh._daily = lambda obs: dict(new_risk_allowed=True, day='x', day_start_equity=10000, daily_net_pnl=0)
    fresh._instrument = lambda: dict(tick='.1', step='.001', minimum='.001', notional='5', market_maximum='100')
    session.get_tickers = lambda **kw: dict(retCode=0, result={'list': [dict(symbol='BTCUSDT', markPrice='100', lastPrice='100', bid1Price='99.9', ask1Price='100.1')]})
    session.get_transaction_log = lambda **kw: pytest.fail('no extra financial collection')
    ctx = fresh.context()
    assert ctx['observed_execution_fee_evidence']['status'] == 'AVAILABLE'
    assert ctx['recorded_scenario_net'] is None
    c = context()
    c.update(observed_execution_fee_evidence=ctx['observed_execution_fee_evidence'])
    raw = wire(c, 'LONG')
    receipt = calculate_candidate(raw, c, candidate_id='c0')
    assert receipt['eligible']
    assert receipt['projection']['whole_plan_payoff']['status'] == 'AVAILABLE'
    original = deepcopy(c)
    original.pop('observed_execution_fee_evidence')
    baseline = calculate_candidate(raw, original, candidate_id='c0')
    assert receipt['raw_risk'] == baseline['raw_risk']
    assert receipt['effective_plan'] == baseline['effective_plan']


@pytest.mark.parametrize('case', ['uid', 'endpoint', 'symbol', 'version', 'future', 'stale', 'maker', 'duplicate', 'control', 'malformed', 'zero_rate', 'future_execution', 'control_uid', 'bad_hash', 'environment'])
def test_bad_cache_is_unknown(tmp_path, case):
    broker, _, observed, children = seeded(tmp_path)
    account(broker, observed, children)
    body = json.loads(cache(broker)[0][1])
    if case in {'uid', 'endpoint', 'symbol', 'version', 'environment'}:
        body[{'uid': 'main_uid'}.get(case, case)] = 'wrong'
    elif case == 'future':
        body['captured_at'] = 100
    elif case == 'stale':
        broker.clock = lambda: 86401
    elif case == 'maker':
        body['evidence']['rates']['MAKER'] = body['evidence']['rates'].pop('TAKER')
    elif case == 'control':
        broker.conn.execute('DELETE FROM llm_scenario_control')
    elif case == 'control_uid':
        broker.conn.execute('UPDATE llm_scenario_control SET body=?', (json.dumps(dict(version=1, state='active', main_uid='456')),))
    elif case == 'zero_rate':
        body['evidence']['rates']['TAKER']['minimum'] = 0
    elif case == 'future_execution':
        body['evidence']['rates']['TAKER']['latest_at'] = 100
    elif case == 'bad_hash':
        body['evidence']['source_sha256'] = 'bad'
    elif case == 'malformed':
        body = []
    broker.conn.execute('UPDATE llm_scenario_broker_evidence SET body=? WHERE kind=?', (json.dumps(body), KIND))
    if case == 'duplicate':
        broker.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', (KIND, .1, json.dumps(body)))
    before = broker.conn.total_changes
    assert broker._flat_fee_evidence(broker.clock())['status'] == 'UNKNOWN'
    assert broker.conn.total_changes == before


def test_replacement_failure_retains_previous_cache_and_other_audit(tmp_path):
    broker, session, observed, children = seeded(tmp_path)
    account(broker, observed, children)
    before = cache(broker)
    executions, transactions = funding_capture_fixture(session)
    for row in executions:
        row['isMaker'] = False
        if row['execType'] == 'Trade':
            row['execFee'] = '.06'
    for row in transactions:
        if row['type'] == 'TRADE':
            row['fee'] = '.06'
    broker.conn.execute(f"CREATE TRIGGER reject_replace BEFORE INSERT ON llm_scenario_broker_evidence WHEN NEW.kind='{KIND}' BEGIN SELECT RAISE(ABORT,'denied'); END")
    assert account(broker, observed, children)['accounting_complete'] is True
    assert cache(broker) == before
    assert broker.conn.execute("SELECT COUNT(*) FROM llm_scenario_broker_evidence WHERE kind='scenario_accounting'").fetchone()[0] == 2


def test_write_failure_preserves_confirmed_accounting(tmp_path):
    broker, _, observed, children = seeded(tmp_path)
    broker.conn.execute(f"CREATE TRIGGER reject_fee BEFORE INSERT ON llm_scenario_broker_evidence WHEN NEW.kind='{KIND}' BEGIN SELECT RAISE(ABORT,'denied'); END")
    result = account(broker, observed, children)
    assert result['accounting_complete'] is True
    assert result['net_pnl'] == pytest.approx(9.7)
    assert cache(broker) == []
    assert broker.conn.execute("SELECT COUNT(*) FROM llm_scenario_broker_evidence WHERE kind='scenario_accounting'").fetchone()[0] == 1


@pytest.mark.parametrize('case', ['maker', 'pending', 'unverified_capture'])
def test_no_numeric_cache_without_confirmed_identity_bound_taker_capture(tmp_path, case):
    broker, session, observed, children = seeded(tmp_path)
    if case == 'maker':
        executions, _ = funding_capture_fixture(session)
        for row in executions:
            row['isMaker'] = True
    elif case == 'pending':
        children[0]['evidence']['order']['cumExecQty'] = '2'
    else:
        evidence = broker.capture_financial_evidence(0, 100)
        broker.capture_financial_evidence = lambda *args: evidence
    account(broker, observed, children)
    assert cache(broker) == []
    assert broker._flat_fee_evidence(.2)['status'] == 'UNKNOWN'


def test_missing_cache_table_and_exact_24h_boundary_are_read_only(tmp_path):
    broker, _, observed, children = seeded(tmp_path)
    account(broker, observed, children)
    earliest = json.loads(cache(broker)[0][1])['evidence']['rates']['TAKER']['earliest_at']
    assert broker._flat_fee_evidence(earliest + 86400)['status'] == 'AVAILABLE'
    assert broker._flat_fee_evidence(earliest + 86400.001)['status'] == 'UNKNOWN'
    broker.conn.execute('DROP TABLE llm_scenario_broker_evidence')
    before = broker.conn.total_changes
    assert broker._flat_fee_evidence(.2)['status'] == 'UNKNOWN'
    assert broker.conn.total_changes == before
