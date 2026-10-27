"""Monitor timing must never change broker evidence or reveal request fields."""
from types import SimpleNamespace

import pytest

from live import scenario_broker
from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker, _MONITOR_CALL_TIMING


def broker_with(method):
    broker = object.__new__(ScenarioDemoBroker)
    broker.session = SimpleNamespace(endpoint='https://api-demo.bybit.com', get_positions=method)
    return broker


def test_trace_is_bounded_and_omits_requests_and_responses():
    broker = broker_with(lambda **kwargs: {'retCode': 0, 'result': {'secret': 'do-not-record'}})
    trace = {'calls': [], 'dropped': 0, 'measurement_failures': 0}
    token = _MONITOR_CALL_TIMING.set(trace)
    try:
        for _ in range(65):
            assert broker._call('get_positions', account='do-not-record')['retCode'] == 0
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    assert len(trace['calls']) == 64 and trace['dropped'] == 1
    assert all(set(x) == {'method', 'elapsed_ms', 'ok'} and x['method'] == 'get_positions' and x['ok'] for x in trace['calls'])
    assert 'do-not-record' not in repr(trace)
    assert _MONITOR_CALL_TIMING.get() is None


def test_transport_failure_remains_sanitized_and_is_timed():
    attempts = []
    def fail(**kwargs):
        attempts.append(1)
        raise TimeoutError('private backend message')
    broker = broker_with(fail)
    trace = {'calls': [], 'dropped': 0, 'measurement_failures': 0}
    token = _MONITOR_CALL_TIMING.set(trace)
    try:
        with pytest.raises(BrokerNotReady, match='broker_transport_unknown'):
            broker._call('get_positions')
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    assert len(trace['calls']) == 1 and trace['calls'][0]['ok'] is False
    assert attempts == [1]
    assert 'private backend message' not in repr(trace)


def test_unrecognized_method_names_are_not_recorded():
    broker = broker_with(lambda **kwargs: {'retCode': 0})
    broker.session.get_private_method = lambda **kwargs: {'retCode': 0}
    trace = {'calls': [], 'dropped': 0, 'measurement_failures': 0}
    token = _MONITOR_CALL_TIMING.set(trace)
    try:
        assert broker._call('get_private_method')['retCode'] == 0
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    assert len(trace['calls']) == 1 and trace['calls'][0]['method'] == 'other'
    assert 'get_private_method' not in repr(trace)


def test_timing_clock_failure_does_not_change_broker_result(monkeypatch):
    broker = broker_with(lambda **kwargs: {'retCode': 0, 'result': {}})
    monkeypatch.setattr(scenario_broker.time, 'perf_counter_ns', lambda: (_ for _ in ()).throw(RuntimeError('clock failed')))
    trace = {'calls': [], 'dropped': 0, 'measurement_failures': 0}
    token = _MONITOR_CALL_TIMING.set(trace)
    try:
        assert broker._call('get_positions')['retCode'] == 0
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    assert trace == {'calls': [], 'dropped': 0, 'measurement_failures': 1}


def test_backward_timing_is_unknown_not_zero(monkeypatch):
    broker = broker_with(lambda **kwargs: {'retCode': 0, 'result': {}})
    values = iter((100, 99))
    monkeypatch.setattr(scenario_broker.time, 'perf_counter_ns', lambda: next(values))
    trace = {'calls': [], 'dropped': 0, 'measurement_failures': 0}
    token = _MONITOR_CALL_TIMING.set(trace)
    try:
        assert broker._call('get_positions')['retCode'] == 0
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    assert trace == {'calls': [], 'dropped': 0, 'measurement_failures': 1}
