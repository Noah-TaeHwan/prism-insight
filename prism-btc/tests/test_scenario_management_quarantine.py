"""Administrative authority never converts missing exchange evidence to truth."""
import pytest

from live.scenario_broker import BrokerNotReady
from tests.test_scenario_immediate_reduce_execution import execution_fixture, held, reduction, auto_fill


@pytest.fixture(name='incident')
def incident_fixture(tmp_path):
    from live.scenario_management_quarantine import digest
    broker, exchange = held(execution_fixture.__wrapped__(tmp_path))
    payload = reduction(broker)
    auto_fill(exchange)
    original = exchange.place_order
    def lost(**request):
        if request.get('orderType') == 'Limit':
            raise TimeoutError('unknown restoration')
        return original(**request)
    exchange.place_order = lost
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    # Fixed incident binding is prepared explicitly, not inferred by registration.
    child = next(c for c in broker.children() if c['status'] == 'UNKNOWN')
    return broker, exchange, child, digest


def test_module_interface_exists_without_mutating_unknown(incident):
    broker, _, child, digest = incident
    from live import scenario_management_quarantine as q
    before = broker.children()
    assert q.active_receipt(broker) is None
    assert broker.children() == before
    assert len(digest(child['request'])) == 64


@pytest.fixture(name='registered')
def registered_fixture(tmp_path):
    from types import SimpleNamespace
    from tests.test_scenario_management_quarantine_runtime import incident as fixture
    generator = fixture.__wrapped__(tmp_path, SimpleNamespace(param='LONG'))
    data = next(generator)
    data['receipt'] = data['authorize']()
    yield data
    try:
        next(generator)
    except StopIteration:
        pass


@pytest.mark.parametrize('change', ['uid', 'endpoint', 'request', 'contract', 'extra_unknown', 'fourth_pending', 'parent', 'local_id', 'created_at'])
def test_invalid_scope_cannot_project_authority(registered, change):
    import json
    data = registered
    broker, q = data['broker'], data['quarantine']
    conn = broker.conn
    if change == 'uid':
        broker.expected_main_uid = '456'
    elif change == 'endpoint':
        data['exchange'].endpoint = 'https://api.bybit.com'
    elif change == 'request':
        child = data['original']['children']
        request = dict(child['request'], qty='99')
        conn.execute('UPDATE llm_scenario_children SET request=? WHERE link_id=?', (json.dumps(request), child['link_id']))
    elif change == 'contract':
        conn.execute("UPDATE llm_scenario_entry_contracts SET body='{}'")
    elif change == 'extra_unknown':
        conn.execute("UPDATE llm_scenario_children SET link_id='unresolved-other',status='UNKNOWN',order_id=NULL,evidence=NULL WHERE kind='entry'")
    elif change in {'parent', 'local_id', 'created_at'}:
        field = {'parent': 'intent_id'}.get(change, change)
        conn.execute(f'UPDATE llm_scenario_children SET {field}=? WHERE link_id=?',
            (1 if change == 'created_at' else 'other', data['original']['children']['link_id']))
    else:
        conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?,NULL)',
                     ('foreign', 'other', '{}', 'PENDING'))
    before = list(data['exchange'].writes)
    with pytest.raises(Exception):
        q.active_receipt(broker)
    assert data['exchange'].writes == before


def test_historical_terminal_child_is_not_a_foreign_live_position(registered):
    import json
    data = registered
    broker, q = data['broker'], data['quarantine']
    # Old fully closed bookkeeping row has no executions and is never queried.
    broker.conn.execute('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('old', 'old-intent', 'old-scenario', 'entry', 'old', '{}', 'TERMINAL', 'old-order',
         json.dumps(dict(executions=[], order=dict(cumExecQty='0', leavesQty='0'))), 1))
    original = broker._query_child
    def query(child):
        assert child['scenario_id'] != 'old-scenario'
        return original(child)
    broker._query_child = query
    assert q.active_receipt(broker) == data['receipt']


def test_approval_and_revocation_are_append_only_without_orders(registered):
    data = registered
    broker, q = data['broker'], data['quarantine']
    before = list(data['exchange'].writes)
    raw = broker.conn.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=?', (q.KIND,)).fetchall()
    q.revoke(broker, approval_id='approved-incident', reason='explicit user revocation')
    assert q.active_receipt(broker) is None
    rows = broker.conn.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=?', (q.KIND,)).fetchall()
    assert rows[:-1] == raw and len(rows) == len(raw) + 1
    assert data['exchange'].writes == before


def test_expired_authority_is_unavailable_but_can_be_explicitly_revoked(registered):
    data = registered
    data['now'][0] += 3601
    q, broker = data['quarantine'], data['broker']
    assert q.active_receipt(broker) is None
    q.revoke(broker, approval_id='approved-incident', reason='revoke expired authorization')
    assert q.active_receipt(broker) is None


def test_null_pending_is_not_an_empty_pending_set():
    import sqlite3
    from live.scenario_management_quarantine import _pending_matches
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT,status TEXT)')
    conn.execute("INSERT INTO llm_scenario_intents VALUES('unexpected','s','{}',NULL)")
    with pytest.raises(ValueError, match='pending_set_changed'):
        _pending_matches(conn, dict(pending_intents=[]))


def test_quarantine_exit_does_not_classify_conditional_restoration_error(registered):
    from live.scenario_management_quarantine import bind_exit
    from tests.test_scenario_execution import persist
    data = registered
    broker, exchange = data['broker'], data['exchange']
    payload = persist(broker, action='EXIT', action_id='close', entries=[])
    bind_exit(broker, payload, data['receipt'])
    broker.conn.commit()
    exchange.max_retries, exchange.force_retry = 3, True
    attempts = []
    def reject(**request):
        attempts.append(request)
        assert (exchange.max_retries, exchange.force_retry) == (1, False)
        return dict(retCode=110093, result={})
    exchange.place_order = reject
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'close')
    assert len(attempts) == 1
    assert (exchange.max_retries, exchange.force_retry) == (3, True)
    child = next(c for c in broker.children() if c['intent_id'] == 'close')
    assert child['status'] == 'UNKNOWN' and child['order_id'] is None
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'close')
    assert len(attempts) == 1


@pytest.mark.parametrize('first', ['timeout', 'retry_code', 'explicit_rejection'])
def test_actual_sdk_quarantine_exit_has_one_transport_attempt(registered, first):
    sdk_module = pytest.importorskip('pybit.unified_trading')
    import requests
    from tests.test_scenario_execution import persist
    data = registered
    broker, exchange, q = data['broker'], data['exchange'], data['quarantine']
    payload = persist(broker, action='EXIT', action_id='sdk-close', entries=[])
    q.bind_exit(broker, payload, data['receipt'])
    broker.conn.commit()
    sdk = sdk_module.HTTP(demo=True, api_key='offline-placeholder', api_secret='offline-placeholder',
                          max_retries=3, force_retry=True, retry_delay=0)
    for name in dir(exchange):
        if name.startswith('get_'):
            setattr(sdk, name, getattr(exchange, name))
    attempts = []
    def send(request, **kwargs):
        attempts.append(request)
        if first == 'timeout':
            raise requests.exceptions.ReadTimeout('ambiguous POST')
        response = requests.Response()
        response.status_code = 200
        code = 10002 if first == 'retry_code' and len(attempts) == 1 else 110093
        response._content = ('{"retCode":%s,"retMsg":"offline","result":{}}' % code).encode()
        return response
    sdk.client.send = send
    broker.session = sdk
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'sdk-close')
    assert len(attempts) == 1
    assert (sdk.max_retries, sdk.force_retry) == (3, True)
    child = next(c for c in broker.children() if c['intent_id'] == 'sdk-close')
    assert child['status'] == 'UNKNOWN' and child['order_id'] is None


@pytest.mark.parametrize('stage', ['configure', 'restore'])
def test_quarantine_exit_retry_property_failure_stays_unknown(registered, stage):
    from tests.test_scenario_execution import persist
    data = registered
    broker, exchange, q = data['broker'], data['exchange'], data['quarantine']
    payload = persist(broker, action='EXIT', action_id='property-close', entries=[])
    q.bind_exit(broker, payload, data['receipt'])
    broker.conn.commit()
    original_type = type(exchange)
    class Session(original_type):
        @property
        def max_retries(self):
            return getattr(self, '_retry_value', 3)

        @max_retries.setter
        def max_retries(self, value):
            if (stage == 'configure' and value == 1) or (stage == 'restore' and value == 3):
                raise RuntimeError('local configuration failure')
            self._retry_value = value
    exchange.__class__ = Session
    attempts = []
    def post(**request):
        attempts.append(request)
        return dict(retCode=110093, result={})
    exchange.place_order = post
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'property-close')
    assert len(attempts) == (0 if stage == 'configure' else 1)
    child = next(c for c in broker.children() if c['intent_id'] == 'property-close')
    assert child['status'] == 'UNKNOWN' and child['evidence'] is None


def test_exact_terminal_source_requires_explicit_release_before_new_model(registered):
    data = registered
    broker, exchange, q = data['broker'], data['exchange'], data['quarantine']
    unknown = data['original']['children']
    # New exact exchange evidence, not an empty lookup or changed local status.
    exchange.place_order(**unknown['request'])
    exchange.orders[unknown['link_id']]['orderStatus'] = 'Cancelled'
    broker._query_child(unknown)
    before = broker.conn.execute('SELECT count(*) FROM llm_scenario_intents').fetchone()[0]
    result = data['runtime'].tick()
    assert result['status'] == 'blocked' and result['reason'] == 'quarantine_release_required'
    assert data['calls'] == []
    assert broker.conn.execute('SELECT count(*) FROM llm_scenario_intents').fetchone()[0] == before
    q.revoke(broker, approval_id='approved-incident', reason='explicit release after exact resolution')
    assert data['runtime'].tick()['status'] == 'wait'
    assert len(data['calls']) == 1


@pytest.mark.parametrize('change', ['fenced', 'opposite', 'nonzero', 'stale', 'confirmed_flat'])
def test_flat_status_does_not_hide_unconfirmed_or_opposite_exposure(registered, change):
    data = registered
    broker, q = data['broker'], data['quarantine']
    observed = dict(exchange_flat=True, legacy_fenced=False, captured_at=data['now'][0],
                    position=dict(size='0', side=''))
    if change == 'fenced':
        observed['legacy_fenced'] = True
    elif change == 'opposite':
        observed['position']['side'] = 'Sell'
    elif change == 'nonzero':
        observed['position']['size'] = '.01'
    elif change == 'stale':
        observed['captured_at'] -= 11
    assert q.projection_failure(broker, observed) == ('FLAT_UNSETTLED' if change == 'confirmed_flat' else 'INVALID')


@pytest.mark.parametrize('status', ['LIVE', 'UNKNOWN', 'BOGUS', None])
@pytest.mark.parametrize('scenario', ['foreign-scenario', None])
def test_global_foreign_unresolved_child_never_grants_management(registered, status, scenario):
    import json
    data = registered
    broker, exchange, q = data['broker'], data['exchange'], data['quarantine']
    request = dict(category='linear', symbol='BTCUSDT', positionIdx=0, orderLinkId='foreign-live',
                   side='Buy', orderType='Limit', qty='.01', price='90', reduceOnly=False)
    exchange.place_order(**request)
    order = exchange.orders['foreign-live']
    conn = broker.conn
    if status is None or scenario is None:
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='llm_scenario_children'").fetchone()[0]
        conn.execute('ALTER TABLE llm_scenario_children RENAME TO fixture_old_children')
        conn.execute(schema.replace('NOT NULL', ''))
        conn.execute('INSERT INTO llm_scenario_children SELECT * FROM fixture_old_children')
        conn.execute('DROP TABLE fixture_old_children')
    conn.execute('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('foreign-live', 'foreign-intent', scenario, 'entry', 'e', json.dumps(request), status,
         order['orderId'], json.dumps(dict(order=order, executions=[])), data['now'][0]))
    observed = broker.capture_account()
    assert observed['legacy_fenced'] is False
    before = list(exchange.writes)
    with pytest.raises(ValueError, match='foreign_unresolved_child'):
        q.project(broker, observed, broker._active(), broker.children('s1'))
    assert exchange.writes == before


@pytest.mark.parametrize('scenario', ['other', None])
@pytest.mark.parametrize('status', ['LIVE_RECONCILED', 'BOGUS'])
def test_foreign_live_reconciled_intent_without_child_is_not_permission(registered, scenario, status):
    broker, q = registered['broker'], registered['quarantine']
    if scenario is None:
        conn = broker.conn
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='llm_scenario_intents'").fetchone()[0]
        conn.execute('ALTER TABLE llm_scenario_intents RENAME TO fixture_old_intents')
        conn.execute(schema.replace('NOT NULL', ''))
        conn.execute('INSERT INTO llm_scenario_intents SELECT * FROM fixture_old_intents')
        conn.execute('DROP TABLE fixture_old_intents')
    broker.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?,NULL)',
                         ('foreign', scenario, '{}', status))
    with pytest.raises(ValueError, match='foreign_unresolved_intent'):
        q.active_receipt(broker)
