"""Real KR/US afternoon trigger batch -> JSON with STEADY_LEADER_TRIGGER_MODE off/observe/live.

Only market-data, clock and network boundaries are mocked; triggers, selection, the T-B detector,
the live merge and JSON export are the production modules. Each batch runs in its own process
(KR and US both own a ``cores`` package). Network is disabled; nothing is sent or ordered. The
candidate ledger writes to a temporary SQLite file. Template: test_pocket_pivot_trigger_integration.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

STEADY_BARS = r'''
def steady_bars(end, scale=1.0, growth=2.0, n=300):
    """Steady uptrend, a 4-session dip to the 20-day line, then an up day on 1.6x down-day volume."""
    base = 50.0 * np.exp(np.linspace(0.0, np.log(growth), n))
    close = base * np.where(np.arange(n) % 2 == 0, 1.003, 0.997)
    close[n - 5:n - 1] = close[n - 6] * np.array([0.992, 0.982, 0.975, 0.972])
    close[n - 1] = close[n - 2] * 1.02
    open_ = np.r_[close[0], close[:-1]] * 1.0005
    open_[-1] = close[-2] * 1.004
    close, open_ = close * scale, open_ * scale
    vol = np.full(n, 1.0e6); vol[-1] = 1.6e6
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.006,
                         "Low": np.minimum(open_, close) * 0.994, "Close": close, "Volume": vol,
                         "Amount": close * vol}, index=pd.bdate_range(end=end, periods=n))

def shaped(frame, ticker, shape):
    if shape == "flat":
        return frame
    if shape == "empty":
        return pd.DataFrame()
    if shape == "ambiguous":
        return pd.concat({"WRONG1": frame, "WRONG2": frame + 1000}, axis=1)
    multi = pd.concat({ticker: frame}, axis=1)
    return multi.swaplevel(axis=1) if shape == "price_ticker" else multi

def test_hooks(batch):
    if os.getenv("TEST_SL_DETECT_FAIL") == "true":
        import prism_core.steady_leader as slmod
        def broken(*a, **k):
            raise ValueError("fixture detector failure")
        slmod.detect = broken
    if os.getenv("TEST_SL_PILOT") == "true":
        batch._get_regime_slots = lambda regime: (1, 0)     # post-FTD pilot: one slot in total
'''

KR_RUN = r'''
import json, os, socket, sys
from unittest.mock import patch
import numpy as np, pandas as pd
import trigger_batch as batch
from cores import market_data
''' + STEADY_BARS + r'''
output, shape = sys.argv[1:3]
test_hooks(batch)
tickers = [f"{i:06d}" for i in range(100001, 100013)]
TB = dict(zip(os.getenv("TEST_SL_TB", "100001,100002,100003").split(","), (2.0, 2.6, 1.6)))
n = len(tickers)
rng = np.random.default_rng(7)
close_prev = np.full(n, 10000.)
chg = np.linspace(0.02, 0.13, n)
rng.shuffle(chg)
close = close_prev * (1 + chg)
openp = close_prev * (1 + rng.uniform(0.0, 0.06, n))
high = np.maximum(close, openp) * (1 + rng.uniform(0.0, 0.03, n))
low = np.minimum(close, openp) * (1 - rng.uniform(0.0, 0.02, n))
vol = rng.integers(2_000_000, 9_000_000, n)
snapshot = pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close,
                         "Volume": vol, "Amount": close * vol}, index=tickers)
previous = pd.DataFrame({"Open": close_prev, "High": close_prev * 1.01, "Low": close_prev * 0.99,
                         "Close": close_prev}, index=tickers)
previous["Volume"] = (vol / rng.uniform(1.5, 6.0, n)).astype(int)
previous["Amount"] = previous["Close"] * previous["Volume"]
cap = pd.DataFrame({"시가총액": rng.uniform(6e11, 5e12, n)}, index=tickers)
bundle = batch.MarketSnapshotBundle(snapshot, previous, cap, "20260915", "KIS_FIXTURE")

def history(start, end, ticker, **kwargs):
    i = tickers.index(ticker)
    if ticker in TB:
        frame = steady_bars("2026-09-16", scale=100.0, growth=TB[ticker])
    else:
        c = np.linspace(7000. + 150 * i, 10000. + 40 * i, 300)
        frame = pd.DataFrame({"Open": c - 10, "High": c + 20, "Low": c - 20, "Close": c,
                              "Volume": 2000000, "Amount": c * 2000000},
                             index=pd.bdate_range(end="2026-09-16", periods=300))
    return shaped(frame, ticker, shape)

def no_network(*a, **k):
    raise AssertionError("Unexpected network/order/LLM in isolated KR batch")

macro = {"market_regime": "sideways", "leading_sectors": [], "sector_map": {}}
with patch.object(socket.socket, "connect", no_network), \
        patch.object(socket, "create_connection", no_network), \
        patch.object(batch, "_resolve_trade_date", return_value="20260916"), \
        patch.object(batch, "load_market_snapshot_bundle", return_value=bundle), \
        patch.object(batch, "_get_ticker_name_map", return_value={s: "N" + s for s in tickers}), \
        patch.object(market_data, "get_market_ohlcv_by_date", side_effect=history):
    result = batch.run_batch("afternoon", "INFO", output, macro_context=macro)
    assert result, "fixture must reach final selection"
    print(json.dumps({k: sorted(map(str, v.index)) for k, v in result.items()}))
'''

US_RUN = r'''
import json, os, socket, sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np, pandas as pd

sys.path.insert(0, str(Path.cwd() / "prism-us"))
import us_trigger_batch as batch
from cores import us_surge_detector as provider
from prism_core import market_intelligence
''' + STEADY_BARS + r'''
class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)

output, shape = sys.argv[1:3]
test_hooks(batch)
if os.getenv("TEST_SL_SESSION_OPEN") == "true":
    import prism_core.steady_leader_trigger as slt
    slt.session_complete = lambda market, day, now=None: False
tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "LLL"]
TB = {"DDD": 2.0, "HHH": 2.6, "KKK": 1.6}
n = len(tickers)
rng = np.random.default_rng(11)
close_prev = np.full(n, 100.)
chg = np.linspace(0.02, 0.13, n)
rng.shuffle(chg)
close = close_prev * (1 + chg)
openp = close_prev * (1 + rng.uniform(0.0, 0.06, n))
high = np.maximum(close, openp) * (1 + rng.uniform(0.0, 0.03, n))
low = np.minimum(close, openp) * (1 - rng.uniform(0.0, 0.02, n))
vol = rng.integers(4_000_000, 9_000_000, n)
snapshot = pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close,
                         "Volume": vol, "Amount": close * vol}, index=tickers)
previous = pd.DataFrame({"Open": close_prev, "High": close_prev * 1.01, "Low": close_prev * 0.99,
                         "Close": close_prev}, index=tickers)
previous["Volume"] = (vol / rng.uniform(1.5, 6.0, n)).astype(int)
previous["Amount"] = previous["Close"] * previous["Volume"]

def download(ticker, **kwargs):
    if isinstance(ticker, str) and ticker in TB:
        frame = steady_bars("2026-09-14", growth=TB[ticker]).drop(columns=["Amount"])
    else:
        i = tickers.index(ticker) if ticker in tickers else 0
        c = np.linspace(70. + 1.5 * i, 100. + 0.4 * i, 300)
        frame = pd.DataFrame({"Open": c - 1, "High": c + 2, "Low": c - 2, "Close": c,
                              "Volume": np.full(300, 2_000_000)},
                             index=pd.bdate_range(end="2026-09-14", periods=300))
    return shaped(frame, ticker, shape)

def no_network(*a, **k):
    raise AssertionError("Unexpected real network access in US batch")

macro = {"market_regime": "sideways", "leading_sectors": []}
with patch.object(socket.socket, "connect", no_network), \
        patch.object(socket, "create_connection", no_network), \
        patch.object(market_intelligence, "datetime", FixedClock), \
        patch.object(batch, "get_major_tickers", return_value=tickers), \
        patch.object(batch, "get_snapshot", return_value=snapshot), \
        patch.object(batch, "get_previous_snapshot", return_value=(previous, "20260911")), \
        patch.object(batch, "get_market_cap_df", return_value=pd.DataFrame(
            {"MarketCap": np.full(n, 5e10)}, index=tickers)), \
        patch.object(provider.yf, "download", side_effect=download), \
        patch.object(provider.yf, "Ticker", side_effect=lambda t: SimpleNamespace(
            info={"shortName": t, "sector": "Technology", "priceToBook": 2.0},
            fast_info={"marketCap": 5e10})):
    result = batch.run_batch("afternoon", "INFO", output, macro_context=macro, override_date="20260914")
    assert result, "fixture must reach final selection"
    print(json.dumps({k: sorted(map(str, v.index)) for k, v in result.items()}))
'''

TB_KEY = {"KR": "꾸준 상승 주도주 눌림", "US": "Steady Leader Pullback"}
TB_TICKERS = {"KR": {"100001", "100002", "100003"}, "US": {"DDD", "HHH", "KKK"}}
_VOLATILE_ROW = {"id", "created_at", "updated_at"}


def _run(tmp_path, market, mode, shape="flat", detect_fail=False, pilot=False, session_open=False,
         tb_tickers=None):
    tag = f"{market}-{mode}-{shape}-{detect_fail}-{pilot}-{session_open}-{tb_tickers}"
    output = tmp_path / f"{tag}.json"
    db = tmp_path / f"{tag}.sqlite"
    env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
               US_SCREENING_UNIVERSE="major_indices",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"),
               REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false",
               TRIGGER_QUALITY_DB=str(tmp_path / "no-tracking.sqlite"),
               CANDIDATE_LEDGER_ENABLED="true", CANDIDATE_LEDGER_DB=str(db),
               TEST_SL_DETECT_FAIL=str(detect_fail).lower(), TEST_SL_PILOT=str(pilot).lower(),
               TEST_SL_SESSION_OPEN=str(session_open).lower())
    if tb_tickers:
        env["TEST_SL_TB"] = tb_tickers
    env.pop("STEADY_LEADER_TRIGGER_MODE", None)
    env.pop("POCKET_PIVOT_TRIGGER_MODE", None)
    if mode is not None:
        env["STEADY_LEADER_TRIGGER_MODE"] = mode
    script = KR_RUN if market == "KR" else US_RUN
    proc = subprocess.run([sys.executable, "-c", script, str(output), shape], cwd=ROOT, env=env,
                          text=True, capture_output=True, timeout=180, check=False)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    payload = json.loads(output.read_text())
    payload["metadata"].pop("run_time", None)
    # Wall-clock capture time; present when the host .env enables market participation (db-server).
    (payload["metadata"].get("market_participation") or {}).pop("captured_at", None)
    (payload["metadata"].get("steady_leader") or {}).get("coverage", {}).pop("elapsed_seconds", None)
    selected = json.loads(proc.stdout.strip().splitlines()[-1])
    rows = []
    if db.exists():
        with sqlite3.connect(db) as connection:
            connection.row_factory = sqlite3.Row
            rows = [{k: v for k, v in dict(r).items() if k not in _VOLATILE_ROW} for r in connection.execute(
                "SELECT * FROM candidate_ledger ORDER BY mode, trigger_type, ticker")]
    return payload, selected, rows, proc.stderr


def _regular(rows):
    return [r for r in rows if not r["mode"].startswith("steady_leader")]


def _steady(rows):
    return [r for r in rows if r["mode"].startswith("steady_leader")]


def _without_steady(payload):
    clone = json.loads(json.dumps(payload))
    clone["metadata"].pop("steady_leader", None)
    return clone


@pytest.mark.parametrize("market", ["KR", "US"])
def test_off_and_unset_are_identical(tmp_path, market):
    unset = _run(tmp_path, market, None)
    off = _run(tmp_path, market, "off")
    bogus = _run(tmp_path, market, "on")
    assert unset[:3] == off[:3] == bogus[:3]
    assert "steady_leader" not in off[0]["metadata"] and TB_KEY[market] not in off[0]
    assert _steady(off[2]) == [] and _regular(off[2])
    assert "unknown STEADY_LEADER_TRIGGER_MODE" in bogus[3]


@pytest.mark.parametrize("market, expected_signal", [("KR", "20260916"), ("US", "20260914")])
def test_observe_records_candidates_without_touching_selection(tmp_path, market, expected_signal):
    off_payload, off_selected, off_rows, _ = _run(tmp_path, market, "off")
    payload, selected, rows, _ = _run(tmp_path, market, "observe")
    assert selected == off_selected
    assert _without_steady(payload) == off_payload
    assert _regular(rows) == off_rows
    block = payload["metadata"]["steady_leader"]
    assert block["mode"] == "observe" and "live" not in block
    assert block["trigger_type"] == "Steady Leader Pullback" and block["trigger_key"] == TB_KEY[market]
    assert block["signal_date"] == expected_signal
    assert block["status_counts"]["PASS"] == 3 and len(block["top_tickers"]) == 2
    assert {c["ticker"] for c in block["candidates"]} == TB_TICKERS[market]
    scores = [c["rank_score"] for c in block["candidates"]]
    assert scores == sorted(scores, reverse=True)
    for c in block["candidates"]:
        assert c["structural_stop"] < c["close"] and c["steadiness"] == 1.0
    steady = _steady(rows)
    assert len(steady) == 3 and all(r["mode"] == "steady_leader_observe" and r["selected"] == 0
                                    for r in steady)
    assert all(r["trigger_type"] == TB_KEY[market] for r in steady)
    iso = f"{expected_signal[:4]}-{expected_signal[4:6]}-{expected_signal[6:]}"
    assert all(r["trade_date"] == iso for r in steady)
    assert "structural_stop_pct" in json.loads(steady[0]["features_json"])


@pytest.mark.parametrize("market", ["KR", "US"])
def test_live_adds_at_most_one_pick_within_the_cap(tmp_path, market):
    off_payload, off_selected, off_rows, _ = _run(tmp_path, market, "off")
    payload, selected, rows, _ = _run(tmp_path, market, "live")
    block = payload["metadata"]["steady_leader"]
    decision = block["live"]
    cap = decision["max_selections"]
    off_tickers = {t for k, v in off_selected.items() for t in v}
    live_tickers = {t for k, v in selected.items() for t in v}
    assert cap == 3 and len(off_tickers) == 3 and len(live_tickers) == 3
    assert decision["applied"] is True and selected[TB_KEY[market]] == [decision["selected_ticker"]]
    assert decision["selected_ticker"] in block["top_tickers"]
    # exactly one regular pick gives way (all three slots were taken), the others are unchanged
    assert len(off_tickers - live_tickers) == 1 and decision["displaced_ticker"] in off_tickers
    assert live_tickers - off_tickers == {decision["selected_ticker"]}
    exported = payload[TB_KEY[market]]
    assert len(exported) == 1 and exported[0]["selection_channel"] == "bottom-up"
    assert exported[0]["steady_leader"]["signal_date"] is not None
    assert exported[0]["steady_leader"]["structural_stop"] > 0
    assert payload["metadata"]["bottomup_count"] == 3
    # ledger: the replaced regular pick is no longer marked selected; the T-B pick is
    regular_selected = {r["ticker"] for r in _regular(rows) if r["selected"]}
    assert regular_selected == off_tickers - {decision["displaced_ticker"]}
    assert {r["ticker"] for r in off_rows if r["selected"]} == off_tickers
    steady = _steady(rows)
    assert {r["mode"] for r in steady} == {"steady_leader_live"}
    assert [r["ticker"] for r in steady if r["selected"]] == [decision["selected_ticker"]]


def test_kr_live_top_candidates_already_selected_changes_nothing(tmp_path):
    """The regular triggers already took both top T-B names: nothing is added or replaced."""
    tb = "100005,100006,100009"
    off = _run(tmp_path, "KR", "off", tb_tickers=tb)
    live = _run(tmp_path, "KR", "live", tb_tickers=tb)
    decision = live[0]["metadata"]["steady_leader"]["live"]
    assert decision["applied"] is False and decision["reason"] == "TOP_ALREADY_SELECTED_OR_UNPRICED"
    assert live[1] == off[1] and _regular(live[2]) == off[2]
    assert _without_steady(live[0]) == off[0]


@pytest.mark.parametrize("market", ["KR", "US"])
def test_live_keeps_the_single_slot_pilot_cap_hard(tmp_path, market):
    off = _run(tmp_path, market, "off", pilot=True)
    live = _run(tmp_path, market, "live", pilot=True)
    assert live[1] == off[1] and sum(len(v) for v in live[1].values()) == 1
    decision = live[0]["metadata"]["steady_leader"]["live"]
    assert decision == {**decision, "applied": False, "reason": "SINGLE_SLOT_CAP_FULL",
                        "max_selections": 1}
    assert _regular(live[2]) == off[2]


@pytest.mark.parametrize("market", ["KR", "US"])
def test_detector_failure_is_fail_open(tmp_path, market):
    off = _run(tmp_path, market, "off")
    for mode in ("observe", "live"):
        broken = _run(tmp_path, market, mode, detect_fail=True)
        assert broken[1] == off[1] and _regular(broken[2]) == off[2]
        assert _without_steady(broken[0]) == off[0]


def test_us_before_close_evaluates_previous_completed_session_only(tmp_path):
    """US afternoon batch runs at 15:10 ET: today's bar is partial, so d = previous session.
    The fixture's T-B bar is dated 09-14 (the batch day) and must not leak into a 09-11 signal."""
    off = _run(tmp_path, "US", "off")
    payload, selected, rows, _ = _run(tmp_path, "US", "observe", session_open=True)
    assert selected == off[1] and _without_steady(payload) == off[0]
    block = payload["metadata"]["steady_leader"]
    assert block["signal_date"] == "20260911" and block["batch_trade_date"] == "20260914"
    assert block["universe_basis"]["session_complete"] is False
    assert block["coverage"]["universe"] == 12
    assert block["status_counts"]["PASS"] == 0 and _steady(rows) == []


@pytest.mark.parametrize("shape", ["price_ticker", "ticker_price"])
def test_us_single_ticker_multiindex_matches_flat(tmp_path, shape):
    flat = _run(tmp_path, "US", "observe")[0]["metadata"]["steady_leader"]
    other = _run(tmp_path, "US", "observe", shape)[0]["metadata"]["steady_leader"]
    assert other == flat


@pytest.mark.parametrize("shape", ["empty", "ambiguous"])
def test_us_unusable_history_is_missing_not_signal(tmp_path, shape):
    off = _run(tmp_path, "US", "off", shape)
    payload, selected, rows, _ = _run(tmp_path, "US", "live", shape)
    block = payload["metadata"]["steady_leader"]
    assert block["status_counts"]["PASS"] == 0 and block["status_counts"]["FAIL"] == 0
    assert block["status_counts"]["MISSING"] == block["coverage"]["fetched"] > 0
    assert block["candidates"] == [] and _steady(rows) == []
    assert block["live"]["applied"] is False and selected == off[1]


# --- KR consumer (real trigger_batch module), provider shapes and session timing -------------

def _kr_steady(end="2026-09-16"):
    n = 300
    base = 50.0 * np.exp(np.linspace(0.0, np.log(2.0), n))
    close = base * np.where(np.arange(n) % 2 == 0, 1.003, 0.997)
    close[n - 5:n - 1] = close[n - 6] * np.array([0.992, 0.982, 0.975, 0.972])
    close[n - 1] = close[n - 2] * 1.02
    open_ = np.r_[close[0], close[:-1]] * 1.0005
    open_[-1] = close[-2] * 1.004
    vol = np.full(n, 1.0e6)
    vol[-1] = 1.6e6
    return pd.DataFrame({"Open": open_ * 100, "High": np.maximum(open_, close) * 100.6,
                         "Low": np.minimum(open_, close) * 99.4, "Close": close * 100, "Volume": vol},
                        index=pd.bdate_range(end=end, periods=n))


def _kr_inputs():
    tickers = ["005930", "000660"]
    snapshot = pd.DataFrame({"Open": [9700., 9700.], "High": [9900., 9900.], "Low": [9600., 9600.],
                             "Close": [9800., 9600.], "Volume": [2e6, 2e6],
                             "Amount": [2e10, 2e10]}, index=tickers)
    previous = snapshot.assign(Close=[9720., 9720.])
    cap = pd.DataFrame({"시가총액": [6e11, 6e11]}, index=tickers)
    return snapshot, previous, cap


@pytest.mark.parametrize("shape, expected", [
    ("flat", "PASS"), ("ticker_price", "PASS"), ("price_ticker", "PASS"),
    ("empty", "MISSING"), ("ambiguous", "MISSING")])
def test_kr_consumer_provider_shapes(monkeypatch, shape, expected):
    import trigger_batch
    from prism_core import steady_leader_trigger as slt

    def history(ticker, end_date, days):
        frame = _kr_steady()
        if shape == "empty":
            return pd.DataFrame()
        if shape == "ambiguous":
            return pd.concat({"WRONG1": frame, "WRONG2": frame + 1000}, axis=1)
        if shape == "flat":
            return frame
        multi = pd.concat({ticker: frame}, axis=1)
        return multi.swaplevel(axis=1) if shape == "price_ticker" else multi

    monkeypatch.setenv("STEADY_LEADER_TRIGGER_MODE", "observe")
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", history)
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(slt, "session_complete", lambda market, day, now=None: True)
    snapshot, previous, cap = _kr_inputs()
    block, universe = trigger_batch._steady_leader_detect("20260916", "20260915", snapshot, previous, cap)
    # 000660 is a down day on the snapshot and is never fetched (up-day prefilter)
    assert block["coverage"]["universe"] == 1 and block["coverage"]["fetched"] == 1
    assert list(universe.index) == ["005930"]
    assert block["status_counts"][expected] == 1
    assert block["top_tickers"] == (["005930"] if expected == "PASS" else [])


def test_kr_consumer_before_close_uses_previous_completed_session(monkeypatch):
    import trigger_batch
    from prism_core import steady_leader_trigger as slt
    calls = []

    def history(ticker, end_date, days):
        calls.append((ticker, end_date, days))
        return _kr_steady(end="2026-09-15")

    monkeypatch.setenv("STEADY_LEADER_TRIGGER_MODE", "live")
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", history)
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(slt, "session_complete", lambda market, day, now=None: False)
    snapshot, previous, cap = _kr_inputs()
    block, _ = trigger_batch._steady_leader_detect("20260916", "20260915", snapshot, previous, cap)
    assert block["mode"] == "live"
    assert block["signal_date"] == "20260915" and block["batch_trade_date"] == "20260916"
    assert {(end, days) for _, end, days in calls} == {("20260915", 300)}
    assert block["universe_basis"]["up_day_prefilter"] is False
    assert block["status_counts"]["PASS"] == 2


def test_kr_consumer_cap_floor_and_off(monkeypatch):
    import trigger_batch
    from prism_core import steady_leader_trigger as slt
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", lambda *a: _kr_steady())
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(slt, "session_complete", lambda market, day, now=None: True)
    snapshot, previous, cap = _kr_inputs()
    monkeypatch.delenv("STEADY_LEADER_TRIGGER_MODE", raising=False)
    assert trigger_batch._steady_leader_detect("20260916", "20260915", snapshot, previous, cap) is None
    monkeypatch.setenv("STEADY_LEADER_TRIGGER_MODE", "observe")
    small = cap.assign(**{"시가총액": [4e11, 4e11]})
    block, _ = trigger_batch._steady_leader_detect("20260916", "20260915", snapshot, previous, small)
    assert block["coverage"]["universe"] == 0 and block["candidates"] == []


def test_kr_live_merge_without_cap_fails_closed():
    import trigger_batch
    final = {"T1": pd.DataFrame({"Close": [1.0]}, index=["A"])}
    block = {"trigger_key": "꾸준 상승 주도주 눌림", "candidates": [], "top_tickers": []}
    snapshot, previous, _ = _kr_inputs()
    assert trigger_batch._steady_leader_live_merge(final, block, snapshot, previous, {}) is final
    assert block["live"] == {"applied": False, "reason": "CAP_UNKNOWN"}
