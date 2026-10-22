"""Real KR/US afternoon trigger batch -> JSON with POCKET_PIVOT_TRIGGER_MODE off/observe/live.

Only market-data, clock and network boundaries are mocked; triggers, selection, the detector and
JSON export are the production modules. Each batch runs in its own process (KR and US both own a
``cores`` package). Network is disabled; nothing is sent or ordered. The candidate ledger writes
to a temporary SQLite file.
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

BASE_BARS = r'''
def base_bars(end, scale=1.0, wiggle=0.2, n=300):
    """Uptrend to 100, a 100-session tight base, then a pocket pivot on ``end``."""
    close = np.r_[np.linspace(50.0, 100.0, 200), np.tile([97.0 - wiggle, 97.0 + wiggle], 50)]
    close[-1] = 98.0
    open_ = close.copy(); open_[-1] = 97.4
    vol = np.full(n, 1_000_000.0); vol[-1] = 1_400_000.0
    close, open_ = close * scale, open_ * scale
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.0075,
                         "Low": np.minimum(open_, close) * 0.9925, "Close": close, "Volume": vol,
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
'''

KR_RUN = r'''
import json, os, socket, sys
from unittest.mock import patch
import numpy as np, pandas as pd
import trigger_batch as batch
from cores import market_data
''' + BASE_BARS + r'''
output, shape = sys.argv[1:3]
if os.getenv("TEST_PP_DETECT_FAIL") == "true":
    import prism_core.pocket_pivot as ppmod
    def broken(*a, **k):
        raise ValueError("fixture detector failure")
    ppmod.detect = broken
tickers = [f"{i:06d}" for i in range(100001, 100013)]
PP = {tickers[0]: 0.2, tickers[1]: 0.6, tickers[2]: 0.4}
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
    if ticker in PP:
        frame = base_bars("2026-09-16", scale=100.0, wiggle=PP[ticker])
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
''' + BASE_BARS + r'''
class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)

output, shape = sys.argv[1:3]
if os.getenv("TEST_PP_DETECT_FAIL") == "true":
    import prism_core.pocket_pivot as ppmod
    def broken(*a, **k):
        raise ValueError("fixture detector failure")
    ppmod.detect = broken
if os.getenv("TEST_PP_SESSION_OPEN") == "true":
    import prism_core.pocket_pivot_trigger as ppt
    ppt.session_complete = lambda market, day, now=None: False
tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "LLL"]
PP = {"AAA": 0.2, "BBB": 0.6, "CCC": 0.4}
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
    if isinstance(ticker, str) and ticker in PP:
        frame = base_bars("2026-09-14", wiggle=PP[ticker]).drop(columns=["Amount"])
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


def _run(tmp_path, market, mode, shape="flat", detect_fail=False, session_open=False):
    tag = f"{market}-{mode}-{shape}-{detect_fail}-{session_open}"
    output = tmp_path / f"{tag}.json"
    db = tmp_path / f"{tag}.sqlite"
    env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
               US_SCREENING_UNIVERSE="major_indices",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"),
               REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false",
               TRIGGER_QUALITY_DB=str(tmp_path / "no-tracking.sqlite"),
               CANDIDATE_LEDGER_ENABLED="true", CANDIDATE_LEDGER_DB=str(db),
               TEST_PP_DETECT_FAIL=str(detect_fail).lower(),
               TEST_PP_SESSION_OPEN=str(session_open).lower())
    # The batches call load_dotenv(); keep a host .env (db-server enables trigger observe modes and
    # market-participation metadata) out of these fixture runs.
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.pop("POCKET_PIVOT_TRIGGER_MODE", None)
    if mode is not None:
        env["POCKET_PIVOT_TRIGGER_MODE"] = mode
    script = KR_RUN if market == "KR" else US_RUN
    proc = subprocess.run([sys.executable, "-c", script, str(output), shape], cwd=ROOT, env=env,
                          text=True, capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    payload = json.loads(output.read_text())
    payload["metadata"].pop("run_time", None)
    # Wall-clock capture time; present when the host .env enables market participation (db-server).
    (payload["metadata"].get("market_participation") or {}).pop("captured_at", None)
    selected = json.loads(proc.stdout.strip().splitlines()[-1])
    rows = []
    if db.exists():
        with sqlite3.connect(db) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(r) for r in connection.execute(
                "SELECT * FROM candidate_ledger WHERE mode = 'pocket_pivot_observe' ORDER BY rank_in_trigger")]
    return payload, selected, rows, proc.stderr


def _without_observe(payload):
    clone = json.loads(json.dumps(payload))
    clone["metadata"].pop("pocket_pivot_observe", None)
    return clone


@pytest.mark.parametrize("market", ["KR", "US"])
def test_off_live_and_unset_are_identical(tmp_path, market):
    unset = _run(tmp_path, market, None)
    off = _run(tmp_path, market, "off")
    live = _run(tmp_path, market, "live")
    assert unset[:3] == off[:3] == live[:3]
    assert "pocket_pivot_observe" not in off[0]["metadata"]
    assert off[2] == []
    assert "live refused" in live[3]


@pytest.mark.parametrize("market, expected_signal", [("KR", "20260916"), ("US", "20260914")])
def test_observe_records_candidates_without_touching_selection(tmp_path, market, expected_signal):
    off_payload, off_selected, _, _ = _run(tmp_path, market, "off")
    payload, selected, rows, _ = _run(tmp_path, market, "observe")
    assert selected == off_selected
    assert _without_observe(payload) == off_payload
    block = payload["metadata"]["pocket_pivot_observe"]
    assert block["mode"] == "observe" and block["live_allowed"] is False
    assert block["trigger_type"] == "Pocket Pivot Base"
    assert block["signal_date"] == expected_signal
    assert block["kr_not_optimized"] is (market == "KR")
    assert block["earnings_within_5d"] is None and "earnings_within_5d" in block["missing"]
    assert block["status_counts"]["PASS"] == 3
    assert len(block["top_tickers"]) == 2
    scores = [c["rank_score"] for c in block["candidates"]]
    assert scores == sorted(scores, reverse=True)
    assert block["top_tickers"] == [c["ticker"] for c in block["candidates"][:2]]
    for c in block["candidates"]:
        assert c["kind"] in {"PP_ONLY", "BOTH"} and c["pivot_252"] > c["pivot_base"]
        assert c["structural_stop"] < c["close"]
    assert len(rows) == 3
    assert all(r["selected"] == 0 and r["trigger_type"] == "Pocket Pivot Base" for r in rows)
    assert all(r["trade_date"] == f"{expected_signal[:4]}-{expected_signal[4:6]}-{expected_signal[6:]}"
               for r in rows)
    features = json.loads(rows[0]["features_json"])
    assert features["earnings_within_5d"] is None and "structural_stop_pct" in features


def test_us_before_close_evaluates_previous_completed_session_only(tmp_path):
    """US afternoon batch runs at 15:10 ET: today's bar is partial, so d = previous session.
    The fixture's PP bar is dated 09-14 (the batch day) and must not leak into a 09-11 signal."""
    off = _run(tmp_path, "US", "off")
    payload, selected, rows, _ = _run(tmp_path, "US", "observe", session_open=True)
    assert selected == off[1] and _without_observe(payload) == off[0]
    block = payload["metadata"]["pocket_pivot_observe"]
    assert block["signal_date"] == "20260911" and block["batch_trade_date"] == "20260914"
    assert block["universe_basis"] == {**block["universe_basis"], "session_complete": False,
                                       "up_day_prefilter": False}
    assert block["coverage"]["universe"] == 12
    assert all(c["signal_date"] == "2026-09-11" for c in block["candidates"])
    assert block["status_counts"]["PASS"] == 0 and rows == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_detector_failure_is_fail_open(tmp_path, market):
    off = _run(tmp_path, market, "off")
    broken = _run(tmp_path, market, "observe", detect_fail=True)
    assert broken[:3] == off[:3]
    assert "[POCKET-PIVOT] observe unavailable" in broken[3]


@pytest.mark.parametrize("shape", ["price_ticker", "ticker_price"])
def test_us_single_ticker_multiindex_matches_flat(tmp_path, shape):
    flat = _run(tmp_path, "US", "observe")[0]["metadata"]["pocket_pivot_observe"]
    other = _run(tmp_path, "US", "observe", shape)[0]["metadata"]["pocket_pivot_observe"]
    for block in (flat, other):
        block["coverage"].pop("elapsed_seconds")
    assert other == flat


@pytest.mark.parametrize("shape", ["empty", "ambiguous"])
def test_us_unusable_history_is_missing_not_signal(tmp_path, shape):
    payload, _, rows, _ = _run(tmp_path, "US", "observe", shape)
    block = payload["metadata"]["pocket_pivot_observe"]
    assert block["status_counts"]["PASS"] == 0 and block["status_counts"]["FAIL"] == 0
    assert block["status_counts"]["MISSING"] == block["coverage"]["fetched"] > 0
    assert block["candidates"] == [] and rows == []


# --- KR consumer (real trigger_batch module), provider shapes and session timing -------------

def _kr_base(end="2026-09-16", wiggle=0.2):
    close = np.r_[np.linspace(50.0, 100.0, 200), np.tile([97.0 - wiggle, 97.0 + wiggle], 50)] * 100
    close[-1] = 9800.0
    open_ = close.copy()
    open_[-1] = 9740.0
    vol = np.full(300, 1_000_000.0)
    vol[-1] = 1_400_000.0
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.0075,
                         "Low": np.minimum(open_, close) * 0.9925, "Close": close, "Volume": vol},
                        index=pd.bdate_range(end=end, periods=300))


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
    from prism_core import pocket_pivot_trigger as ppt

    def history(ticker, end_date, days):
        frame = _kr_base()
        if shape == "empty":
            return pd.DataFrame()
        if shape == "ambiguous":
            return pd.concat({"WRONG1": frame, "WRONG2": frame + 1000}, axis=1)
        if shape == "flat":
            return frame
        multi = pd.concat({ticker: frame}, axis=1)
        return multi.swaplevel(axis=1) if shape == "price_ticker" else multi

    monkeypatch.setenv("POCKET_PIVOT_TRIGGER_MODE", "observe")
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", history)
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(ppt, "session_complete", lambda market, day, now=None: True)
    snapshot, previous, cap = _kr_inputs()
    block = trigger_batch._pocket_pivot_observe("20260916", "20260915", snapshot, previous, cap, None)
    # 000660 is a down day on the snapshot and is never fetched (PP1 prefilter)
    assert block["coverage"]["universe"] == 1 and block["coverage"]["fetched"] == 1
    assert block["status_counts"][expected] == 1
    assert block["top_tickers"] == (["005930"] if expected == "PASS" else [])


def test_kr_consumer_before_close_uses_previous_completed_session(monkeypatch):
    import trigger_batch
    from prism_core import pocket_pivot_trigger as ppt
    calls = []

    def history(ticker, end_date, days):
        calls.append((ticker, end_date))
        return _kr_base(end="2026-09-15")

    monkeypatch.setenv("POCKET_PIVOT_TRIGGER_MODE", "observe")
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", history)
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(ppt, "session_complete", lambda market, day, now=None: False)
    snapshot, previous, cap = _kr_inputs()
    block = trigger_batch._pocket_pivot_observe("20260916", "20260915", snapshot, previous, cap, None)
    assert block["signal_date"] == "20260915" and block["batch_trade_date"] == "20260916"
    assert {end for _, end in calls} == {"20260915"}
    assert block["universe_basis"]["up_day_prefilter"] is False
    assert block["status_counts"]["PASS"] == 2


def test_kr_consumer_cap_floor_and_off(monkeypatch):
    import trigger_batch
    from prism_core import pocket_pivot_trigger as ppt
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", lambda *a: _kr_base())
    monkeypatch.setattr(trigger_batch, "_get_ticker_name_map", lambda: {})
    monkeypatch.setattr(ppt, "session_complete", lambda market, day, now=None: True)
    snapshot, previous, cap = _kr_inputs()
    monkeypatch.delenv("POCKET_PIVOT_TRIGGER_MODE", raising=False)
    assert trigger_batch._pocket_pivot_observe("20260916", "20260915", snapshot, previous, cap, None) is None
    monkeypatch.setenv("POCKET_PIVOT_TRIGGER_MODE", "observe")
    small = cap.assign(**{"시가총액": [4e11, 4e11]})
    block = trigger_batch._pocket_pivot_observe("20260916", "20260915", snapshot, previous, small, None)
    assert block["coverage"]["universe"] == 0 and block["candidates"] == []


def test_mode_resolution_and_session_clock(monkeypatch):
    from datetime import datetime, timezone
    from prism_core import pocket_pivot_trigger as ppt
    for raw, expected in (("", "off"), ("off", "off"), ("observe", "observe"), (" OBSERVE ", "observe"),
                          ("live", "off"), ("on", "off")):
        monkeypatch.setenv("POCKET_PIVOT_TRIGGER_MODE", raw)
        assert ppt.resolve_mode() == expected
    # US close 16:00 ET = 20:00 UTC on 2026-09-14 (EDT); KR close 15:30 KST = 06:30 UTC
    assert ppt.session_complete("US", "20260914", datetime(2026, 9, 14, 19, 10, tzinfo=timezone.utc)) is False
    assert ppt.session_complete("US", "20260914", datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)) is True
    assert ppt.session_complete("KR", "20260916", datetime(2026, 9, 16, 6, 40, tzinfo=timezone.utc)) is True
    assert ppt.session_complete("KR", "20260916", datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)) is False
    assert ppt.session_complete("US", "20260911", datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)) is True
