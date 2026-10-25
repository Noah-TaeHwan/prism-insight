"""Only fresh, explicit conditional restoration rejection proves no order exists."""
import sys
import types

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from live.scenario_immediate_reduce import load
from tests.test_scenario_execution import persist
from tests.test_scenario_immediate_reduce_execution import (
    execution_fixture, auto_fill, reduction,
)


@pytest.fixture(name='live')
def live_fixture(tmp_path):
    return execution_fixture.__wrapped__(tmp_path)


def prepared(live):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id='tp', price=105, fraction=1)],
                   partial_stops=[dict(id='ps', price=99, fraction=.5)])
    broker.execute(plan, 'a1')
    exchange.fill(broker.children()[0]['link_id'], .1)
    broker.reconcile()
    payload = reduction(broker)
    auto_fill(exchange)
    return broker, exchange, payload


@pytest.mark.parametrize('code', [110092, 110093])
def test_explicit_nonacceptance_preserves_attempt_without_fake_order(live, code):
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    attempts = []
    def place(**request):
        if request.get('triggerPrice'):
            attempts.append(request)
            return dict(retCode=code, retMsg='trigger direction invalid', result={})
        return original(**request)
    exchange.place_order = place
    broker.execute(payload, 'r1')
    body = load(broker, 'r1')
    assert body['status'] == 'RESTORE_FAILED'
    assert len(body['rejected_restorations']) == 1
    assert len(attempts) == 1
    assert not any(c['intent_id'] == 'r1' and c['kind'] == 'partial_sl' for c in broker.children())
    assert exchange.stop == '98'
    broker = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid='123',
                               clock=broker.clock, execution_enabled=True)
    broker.execute(payload, 'r1')
    broker.reconcile()
    assert len(attempts) == 1
    exit_plan = persist(broker, action='EXIT', action_id='exit', entries=[])
    broker.execute(exit_plan, 'exit')
    assert any(c['kind'] == 'exit' for c in broker.children())


@pytest.mark.parametrize('code', [10000, 110072, 10001])
def test_other_codes_remain_unknown_and_never_repost(live, code):
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    attempts = []
    def place(**request):
        if request.get('triggerPrice'):
            attempts.append(request)
            return dict(retCode=code, retMsg='not accepted proof unavailable', result={})
        return original(**request)
    exchange.place_order = place
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    assert len(attempts) == 1
    assert load(broker, 'r1')['status'] == 'RESTORING'
    assert any(c['intent_id'] == 'r1' and c['status'] == 'UNKNOWN' for c in broker.children())


def test_crossed_restore_mark_skips_post_but_keeps_full_stop(live):
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    attempts = []
    def place(**request):
        attempts.append(request)
        result = original(**request)
        if request.get('timeInForce') == 'IOC':
            exchange.get_tickers = lambda **kw: dict(retCode=0, result={'list': [dict(
                symbol='BTCUSDT', markPrice='98.5', lastPrice='98.5', nextFundingTime='1800003600000')]})
        return result
    exchange.place_order = place
    broker.execute(payload, 'r1')
    assert not any(r.get('triggerPrice') for r in attempts)
    assert load(broker, 'r1')['status'] == 'RESTORE_FAILED'
    assert exchange.stop == '98'


@pytest.mark.parametrize('typed,code', [(True, 110092), (True, 110093), (True, 110072), (False, 110093)])
def test_sdk_exception_type_and_code_both_required(live, monkeypatch, typed, code):
    # Offline stand-in for the optional SDK's concrete exception type.
    module = types.ModuleType('pybit.exceptions')
    class InvalidRequestError(Exception):
        def __init__(self):
            self.status_code, self.message, self.request, self.time = code, 'rejected', 'exact SDK request', 'now'
    module.InvalidRequestError = InvalidRequestError
    monkeypatch.setitem(sys.modules, 'pybit.exceptions', module)
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    def place(**request):
        if request.get('triggerPrice'):
            if typed:
                raise InvalidRequestError()
            raise RuntimeError(f'InvalidRequestError: {code}')
        return original(**request)
    exchange.place_order = place
    if typed and code in {110092, 110093}:
        broker.execute(payload, 'r1')
        body = load(broker, 'r1')
        assert body['status'] == 'RESTORE_FAILED'
        proof = next(iter(body['rejected_restorations'].values()))['proof']
        assert proof['source'] == 'PYBIT_INVALID_REQUEST'
        assert proof['response']['request'] == 'exact SDK request'
    else:
        with pytest.raises(BrokerNotReady):
            broker.execute(payload, 'r1')
        assert load(broker, 'r1')['status'] == 'RESTORING'


@pytest.mark.parametrize('failure', ['archive', 'delete'])
def test_atomic_archive_failure_keeps_original_unknown(live, failure):
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    def place(**request):
        if request.get('triggerPrice'):
            if failure == 'archive':
                broker.conn.execute("CREATE TRIGGER fail_archive BEFORE INSERT ON llm_scenario_reductions WHEN NEW.body LIKE '%rejected_restorations%' BEGIN SELECT RAISE(ABORT,'archive failed'); END")
            else:
                broker.conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON llm_scenario_children WHEN OLD.kind='partial_sl' BEGIN SELECT RAISE(ABORT,'delete failed'); END")
            return dict(retCode=110093, result={})
        return original(**request)
    exchange.place_order = place
    with pytest.raises(Exception, match='failed'):
        broker.execute(payload, 'r1')
    assert 'rejected_restorations' not in load(broker, 'r1')
    assert any(c['kind'] == 'partial_sl' and c['status'] == 'UNKNOWN' and c['order_id'] is None for c in broker.children())
    assert exchange.stop == '98'


def test_existing_unknown_is_not_reinterpreted_by_new_rejection_policy(live):
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    calls = []
    def lost(**request):
        if request.get('triggerPrice'):
            calls.append(request)
            raise TimeoutError()
        return original(**request)
    exchange.place_order = lost
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    before = [c for c in broker.children() if c['status'] == 'UNKNOWN']
    exchange.place_order = lambda **kw: pytest.fail('old UNKNOWN must never POST again')
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    assert [c for c in broker.children() if c['status'] == 'UNKNOWN'] == before
    assert 'rejected_restorations' not in load(broker, 'r1')


def test_saved_rejection_corruption_fails_closed(live):
    from live.scenario_immediate_reduce import _save
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    exchange.place_order = lambda **kw: dict(retCode=110093, result={}) if kw.get('triggerPrice') else original(**kw)
    broker.execute(payload, 'r1')
    body = load(broker, 'r1')
    next(iter(body['rejected_restorations'].values()))['proof']['request']['qty'] = '100'
    _save(broker, 'r1', body)
    writes = list(exchange.writes)
    with pytest.raises(BrokerNotReady, match='reduce_rejection_corrupt'):
        broker.execute(payload, 'r1')
    assert exchange.writes == writes


def test_crash_after_archive_restarts_without_repost(live, monkeypatch):
    from live import scenario_immediate_reduce as module
    broker, exchange, payload = prepared(live)
    original = exchange.place_order
    attempts = []
    def reject(**kw):
        if kw.get('triggerPrice'):
            attempts.append(kw)
            return dict(retCode=110093, result={})
        return original(**kw)
    exchange.place_order = reject
    observe = module._observation
    def crash(b, body, **kwargs):
        if body.get('rejected_restorations'):
            raise RuntimeError('crash after durable archive')
        return observe(b, body, **kwargs)
    monkeypatch.setattr(module, '_observation', crash)
    with pytest.raises(RuntimeError, match='crash after durable'):
        broker.execute(payload, 'r1')
    assert load(broker, 'r1')['status'] == 'RESTORING'
    monkeypatch.setattr(module, '_observation', observe)
    broker = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid='123',
                               clock=broker.clock, execution_enabled=True)
    broker.execute(payload, 'r1')
    assert load(broker, 'r1')['status'] == 'RESTORE_FAILED'
    assert len(attempts) == 1
    assert exchange.stop == '98'


@pytest.mark.parametrize('outcome', ['success', 'timeout', 'explicit_rejection'])
@pytest.mark.parametrize('attributes_present', [True, False])
def test_restoration_disables_sdk_retries_only_during_its_post(live, outcome, attributes_present):
    broker, exchange, payload = prepared(live)
    if attributes_present:
        exchange.max_retries, exchange.force_retry = 3, True
    original = exchange.place_order
    settings = []
    def place(**request):
        if request.get('triggerPrice'):
            settings.append((getattr(exchange, 'max_retries', None), getattr(exchange, 'force_retry', None)))
            if outcome == 'timeout':
                raise TimeoutError('first transport attempt ambiguous')
            if outcome == 'explicit_rejection':
                return dict(retCode=110093, result={})
        return original(**request)
    exchange.place_order = place
    if outcome == 'timeout':
        with pytest.raises(BrokerNotReady):
            broker.execute(payload, 'r1')
        assert load(broker, 'r1')['status'] == 'RESTORING'
    else:
        broker.execute(payload, 'r1')
    assert settings == [(1, False)]
    if attributes_present:
        assert (exchange.max_retries, exchange.force_retry) == (3, True)
    else:
        assert not hasattr(exchange, 'max_retries')
        assert not hasattr(exchange, 'force_retry')


def test_hidden_ambiguous_attempt_cannot_be_followed_by_classifiable_rejection(live):
    broker, exchange, payload = prepared(live)
    exchange.max_retries, exchange.force_retry = 3, True
    original = exchange.place_order
    transport_attempts = []
    def place(**request):
        if not request.get('triggerPrice'):
            return original(**request)
        for attempt in range(exchange.max_retries):
            transport_attempts.append(attempt)
            if attempt == 0:
                if exchange.force_retry:
                    continue
                raise TimeoutError('unknown first POST')
            return dict(retCode=110093, result={})
        raise TimeoutError('attempt bound')
    exchange.place_order = place
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, 'r1')
    assert transport_attempts == [0]
    assert 'rejected_restorations' not in load(broker, 'r1')
    assert (exchange.max_retries, exchange.force_retry) == (3, True)


@pytest.mark.parametrize('first_response', ['timeout', 'retry_code', 'rejection'])
def test_actual_sdk_single_transport_attempt_offline(live, first_response):
    sdk_module = pytest.importorskip('pybit.unified_trading')
    import requests
    broker, exchange, payload = prepared(live)
    sdk = sdk_module.HTTP(demo=True, api_key='offline-placeholder', api_secret='offline-placeholder',
                          max_retries=3, force_retry=True, retry_delay=0)
    for name in dir(exchange):
        if name.startswith('get_') or name in {'cancel_order', 'set_trading_stop', 'amend_order'}:
            setattr(sdk, name, getattr(exchange, name))
    sdk_place, original = sdk.place_order, exchange.place_order
    sdk.place_order = lambda **kw: sdk_place(**kw) if kw.get('triggerPrice') else original(**kw)
    sends = []
    def send(request, **kwargs):
        sends.append(request)
        if len(sends) == 1 and first_response == 'timeout':
            raise requests.exceptions.ReadTimeout('ambiguous first POST')
        response = requests.Response()
        response.status_code = 200
        code = 10002 if len(sends) == 1 and first_response == 'retry_code' else 110093
        response._content = ('{"retCode":%s,"retMsg":"offline fixture","result":{}}' % code).encode()
        return response
    sdk.client.send = send  # No socket/network call is permitted.
    broker.session = sdk
    if first_response == 'rejection':
        broker.execute(payload, 'r1')
        assert load(broker, 'r1')['status'] == 'RESTORE_FAILED'
    else:
        with pytest.raises(BrokerNotReady):
            broker.execute(payload, 'r1')
        assert 'rejected_restorations' not in load(broker, 'r1')
    assert len(sends) == 1
    assert (sdk.max_retries, sdk.force_retry) == (3, True)


@pytest.mark.parametrize('stage', ['configure', 'restore'])
def test_typed_exception_from_retry_property_is_not_exchange_rejection(live, monkeypatch, stage):
    module = types.ModuleType('pybit.exceptions')
    class InvalidRequestError(Exception):
        status_code = 110093
        message = 'local property failed, not exchange response'
        request = 'none'
        time = 'now'
    module.InvalidRequestError = InvalidRequestError
    monkeypatch.setitem(sys.modules, 'pybit.exceptions', module)
    broker, exchange, payload = prepared(live)
    original_type = type(exchange)
    class PropertySession(original_type):
        @property
        def max_retries(self):
            return getattr(self, '_retry_value', 3)

        @max_retries.setter
        def max_retries(self, value):
            if (stage == 'configure' and value == 1) or (stage == 'restore' and value == 3):
                raise InvalidRequestError()
            self._retry_value = value
    exchange.__class__ = PropertySession
    original = exchange.place_order
    attempts = []
    def place(**request):
        if request.get('triggerPrice'):
            attempts.append(request)
            raise InvalidRequestError()
        return original(**request)
    exchange.place_order = place
    with pytest.raises(BrokerNotReady, match='submission_unknown'):
        broker.execute(payload, 'r1')
    assert len(attempts) == (0 if stage == 'configure' else 1)
    assert 'rejected_restorations' not in load(broker, 'r1')
    assert any(c['kind'] == 'partial_sl' and c['status'] == 'UNKNOWN'
               for c in broker.children()) is (stage == 'restore')


def test_nonrestoration_orders_keep_original_retry_configuration(live):
    broker, exchange, payload = prepared(live)
    exchange.max_retries, exchange.force_retry = 3, True
    original = exchange.place_order
    seen = []
    def place(**request):
        seen.append((bool(request.get('triggerPrice')), exchange.max_retries, exchange.force_retry))
        return original(**request)
    exchange.place_order = place
    broker.execute(payload, 'r1')
    assert any(not conditional for conditional, _, _ in seen)
    assert all((retries, force) == ((1, False) if conditional else (3, True))
               for conditional, retries, force in seen)
    assert (exchange.max_retries, exchange.force_retry) == (3, True)
