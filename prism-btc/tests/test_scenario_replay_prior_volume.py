"""Missing prior minute must not be silently treated as zero liquidity."""
from types import SimpleNamespace

import pandas as pd
import pytest

from analysis.scenario_replay import Driver, network_boundary, run_replay
from tests.test_scenario_replay import inputs


def market_with_minutes(minutes, volumes):
    index = pd.to_datetime(minutes, unit='ms', utc=True)
    frame = pd.DataFrame(
        dict(open=100., high=101., low=99., close=100., volume=volumes), index=index
    )
    return SimpleNamespace(source=frame, mark=frame.copy())


def driver(market, start=120000, end=180000):
    return Driver({'start_ms': start, 'end_ms': end, 'funding': []},
                  market, None, 'OHLC', None, None)


def test_previous_completed_volume_is_used_and_observed_zero_stays_zero():
    assert driver(market_with_minutes([60000, 120000], [1., 99.])).capacity[120000] == 10
    assert driver(market_with_minutes([60000, 120000], [0., 99.])).capacity[120000] == 0


@pytest.mark.parametrize('minutes,volumes', [
    ([120000], [99.]),
    ([0, 120000], [1., 99.]),
    ([60000, 120000], [float('nan'), 99.]),
    ([60000, 120000], [-1., 99.]),
])
def test_absent_stale_or_invalid_previous_volume_is_not_zero(minutes, volumes):
    with pytest.raises(ValueError, match='replay_previous_minute_volume_'):
        driver(market_with_minutes(minutes, volumes))


def test_pipeline_stops_before_model_when_previous_minute_is_missing(tmp_path):
    bundle, market = inputs()
    missing = pd.Timestamp(bundle['start_ms'] - 60000, unit='ms', tz='UTC')
    market.source = market.source.drop(missing)
    calls = []
    def policy(*args):
        calls.append(args)
        raise AssertionError('model must not be called')
    with network_boundary('frozen') as blocked:
        with pytest.raises(ValueError, match='replay_previous_minute_volume_missing'):
            run_replay(bundle, market, tmp_path/'out', mode='fixture',
                       tape_path=tmp_path/'tape', policy=policy)
    assert calls == []
    assert blocked == []


def test_replay_cannot_silently_end_with_missing_last_minute():
    market = market_with_minutes([60000, 120000], [1., 1.])
    with pytest.raises(ValueError, match='replay_minute_window_incomplete'):
        driver(market, start=120000, end=240000)


def test_replay_cannot_accept_entirely_missing_window():
    market = market_with_minutes([60000], [1.])
    with pytest.raises(ValueError, match='replay_minute_window_incomplete'):
        driver(market, start=120000, end=180000)


def test_pipeline_stops_before_model_when_last_minute_is_missing(tmp_path):
    bundle, market = inputs()
    missing = pd.Timestamp(bundle['end_ms'] - 60000, unit='ms', tz='UTC')
    market.source = market.source.drop(missing)
    market.mark = market.mark.drop(missing)
    calls = []
    def policy(*args):
        calls.append(args)
        raise AssertionError('model must not be called')
    with network_boundary('frozen') as blocked:
        with pytest.raises(ValueError, match='replay_minute_window_incomplete'):
            run_replay(bundle, market, tmp_path/'out', mode='fixture',
                       tape_path=tmp_path/'tape', policy=policy)
    assert calls == []
    assert blocked == []
