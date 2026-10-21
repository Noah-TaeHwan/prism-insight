"""Base pocket pivot detector (prism_core.pocket_pivot): synthetic bars only, no network."""
import math

import numpy as np
import pandas as pd
import pytest

from prism_core import pocket_pivot as pp

N_BARS = 300
BASE_START = 200          # highest close (100) at BASE_START - 1


def _bars(close, open_=None, volume=None, start="2025-01-01"):
    """Fresh OHLCV frame from arrays (High/Low hug the open/close by 0.75%)."""
    close = np.asarray(close, dtype=float)
    open_ = close.copy() if open_ is None else np.asarray(open_, dtype=float)
    volume = np.full(len(close), 1_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.0075,
                         "Low": np.minimum(open_, close) * 0.9925, "Close": close, "Volume": volume},
                        index=pd.bdate_range(start, periods=len(close)))


def _base_arrays():
    """Uptrend to a 100 close, then a 100-session tight base at 96.8/97.2, then the PP day."""
    close = np.r_[np.linspace(50.0, 100.0, BASE_START),
                  np.tile([96.8, 97.2], (N_BARS - BASE_START) // 2)]
    close[-1] = 98.0
    open_ = close.copy()
    open_[-1] = 97.4
    volume = np.full(N_BARS, 1_000_000.0)
    volume[-1] = 1_400_000.0
    return close, open_, volume


def _clean_base(overrides=None):
    close, open_, volume = _base_arrays()
    arrays = {"Close": close, "Open": open_, "Volume": volume}
    for (column, position), value in (overrides or {}).items():
        arrays[column][position] = value
    return _bars(close, open_, volume)


def test_clean_base_pocket_pivot_passes_with_recorded_fields():
    result = pp.detect(_clean_base(), market="US")
    assert result["status"] == "PASS", result["reason_codes"]
    m = result["metrics"]
    assert m["base_length"] == N_BARS - BASE_START
    assert m["pivot_252"] == pytest.approx(100.0)
    assert m["kind"] == "PP_ONLY"
    assert m["pp_seq_in_base"] == 1
    assert m["depth_bucket"] == "<=33"
    assert m["vol_dryup_10_50"] == pytest.approx(1.0)
    assert m["signature_strength"] == pytest.approx(1.4)
    assert m["pp5_ratio_atr"] < 3.0
    assert result["diag_codes"] == []


def test_structural_stop_and_pivots_follow_design_v01():
    m = pp.detect(_clean_base(), market="US")["metrics"]
    # pivot_base = highest close of the prior 40 sessions; stop = their lowest low x 0.99
    assert m["pivot_base"] == pytest.approx(97.2)
    assert m["structural_stop"] == pytest.approx(96.8 * 0.9925 * 0.99)
    assert m["structural_stop_pct"] == pytest.approx((96.8 * 0.9925 * 0.99 / 98.0 - 1) * 100)
    assert m["pivot_base_vs_close_pct"] == pytest.approx((97.2 / 98.0 - 1) * 100)
    assert m["pivot_252"] == pytest.approx(100.0)


@pytest.mark.parametrize("override, code", [
    ({("Close", -1): 96.5, ("Open", -1): 96.6}, "NOT_UP_DAY"),
    ({("Volume", -1): 900_000.0}, "NO_VOLUME_SIGNATURE"),
    ({("Close", 280): 101.0}, "BASE_TOO_SHORT"),
    ({("Open", -1): 101.0, ("Close", -1): 104.0}, "GAP_OR_SURGE"),
])
def test_each_gate_counterexample_fails_with_its_code(override, code):
    result = pp.detect(_clean_base(override), market="US")
    assert result["status"] == "FAIL"
    assert code in result["reason_codes"]


def test_no_down_day_is_no_signal():
    close, open_, volume = _base_arrays()
    close[-17:-1] = np.linspace(96.0, 97.0, 16)
    open_[-17:-1] = close[-17:-1]
    result = pp.detect(_bars(close, open_, volume), market="US")
    assert result["status"] == "FAIL"
    assert "NO_DOWN_DAY" in result["reason_codes"]
    assert result["metrics"]["signature_strength"] is None


def test_below_ma50_fails():
    close, open_, volume = _base_arrays()
    close[-2], close[-1], open_[-2], open_[-1] = 90.0, 91.0, 90.0, 90.5
    result = pp.detect(_bars(close, open_, volume), market="US")
    assert "BELOW_MA50" in result["reason_codes"]


def test_drifting_base_fails_contraction():
    close, open_, volume = _base_arrays()
    close[-21:-1] = np.linspace(88.0, 97.0, 20)
    open_[-21:-1] = close[-21:-1]
    result = pp.detect(_bars(close, open_, volume), market="US")
    assert "NOT_CONTRACTED_PRICE" in result["reason_codes"]
    assert result["metrics"]["pp5_ratio_atr"] >= 3.0


@pytest.mark.parametrize("bars, signal_date, code", [
    (_clean_base().iloc[:50], None, "SHORT_HISTORY"),
    (_clean_base(), "2030-01-01", "NO_BAR"),
    (pd.DataFrame(), None, "NO_HISTORY"),
    (_clean_base().drop(columns=["Volume"]), None, "BAD_COLUMNS"),
])
def test_missing_is_unknown_not_fail(bars, signal_date, code):
    result = pp.detect(bars, market="US", signal_date=signal_date)
    assert result["status"] == "MISSING"
    assert result["reason_codes"] == [code]


def test_zero_volume_bar_is_missing():
    assert pp.detect(_clean_base({("Volume", -1): 0.0}))["reason_codes"] == ["BAD_BAR"]


def test_signal_date_uses_only_bars_up_to_that_session():
    frame = _clean_base()
    extended = pd.concat([frame, frame.iloc[[-1]].set_axis([frame.index[-1] + pd.offsets.BDay()])])
    extended.iloc[-1, extended.columns.get_loc("Close")] = 120.0
    assert pp.detect(extended, signal_date=frame.index[-1])["status"] == "PASS"


def test_market_constants_and_live_guard():
    assert pp.MARKET_PARAMS["US"] == pp.PocketPivotParams(40, 3.0, True)
    assert pp.MARKET_PARAMS["KR"].base_min == 40 and pp.MARKET_PARAMS["KR"].pp5_max == 3.0
    assert pp.MARKET_PARAMS["KR"].optimized is False and pp.KR_NOT_OPTIMIZED is True
    assert pp.live_allowed("US") == (False, "L2_L3_NOT_IMPLEMENTED")
    allowed, reason = pp.live_allowed("kr")
    assert allowed is False and reason.startswith("KR_NO_EVIDENCE")
    assert pp.detect(_clean_base(), market="KR")["status"] == "PASS"


def test_ranking_contraction_and_rs_top_two():
    rows = [{"ticker": "A", "pp5_ratio_atr": 1.0, "rs": 10},
            {"ticker": "B", "pp5_ratio_atr": 2.0, "rs": 95},
            {"ticker": "C", "pp5_ratio_atr": 2.5, "rs": None},
            {"ticker": "D", "pp5_ratio_atr": 0.5, "rs": 80}]
    ranked = pp.rank_candidates(rows)
    scores = {r["ticker"]: r["rank_score"] for r in ranked}
    # contraction percentile: D 1.0, A 0.75, B 0.5, C 0.25 (tighter = higher)
    assert scores == pytest.approx({"D": 0.9, "B": 0.725, "A": 0.425, "C": 0.125})
    assert [r["ticker"] for r in ranked] == ["D", "B", "A", "C"]
    assert [r["top"] for r in ranked] == [True, True, False, False]
    assert pp.rank_candidates([]) == []


# --- parity with the research implementation (tasks/research-20261022/base-pp) -------------

def _research_series(seed, n=320):
    r = np.random.default_rng(seed)
    ret = r.normal(0.0005, 0.02, n)
    ret[r.integers(0, n, n // 3)] *= 0.2
    c = 100 * np.exp(np.cumsum(ret))
    o = c * (1 + r.normal(0, 0.006, n))
    h = np.maximum(o, c) * (1 + abs(r.normal(0, 0.01, n)))
    lo = np.minimum(o, c) * (1 - abs(r.normal(0, 0.01, n)))
    v = r.lognormal(14, 0.5, n)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v},
                        index=pd.bdate_range("2020-01-01", periods=n))


# Values produced by pp_detect_v11.detect_base_v11 (book defaults PP0 25, PP5 4.5) on the same
# series: (seed, date, status, gate codes, pp5_ratio_atr, base_length, base_depth,
# signature_strength, kind, RISING_WEDGE, V_SPIKE_V2). 13,350 cases matched offline.
GOLDEN_V11 = [
    (0, "2020-12-30", "FAIL", ["NOT_UP_DAY", "NO_VOLUME_SIGNATURE"], 3.1940723812, 169, 0.2659245732, 0.6728955417, "NONE", False, False),
    (0, "2020-12-31", "FAIL", ["BELOW_MA50", "NOT_UP_DAY", "NO_VOLUME_SIGNATURE"], 3.1627175752, 170, 0.2659245732, 0.543196862, "NONE", False, False),
    (0, "2021-01-05", "FAIL", ["BELOW_MA50", "NO_VOLUME_SIGNATURE"], 2.1656031939, 173, 0.2659245732, 0.2134596773, "NONE", False, False),
    (0, "2021-01-27", "PASS", [], 3.5874971787, 189, 0.2659245732, 1.0241616837, "PP_ONLY", False, False),
    (2, "2021-03-03", "PASS", [], 2.8790462851, 245, 0.3165614987, 1.1538614597, "PP_ONLY", False, False),
    (2, "2021-03-23", "PASS", [], 2.9314062737, 237, 0.315937088, 1.0109854002, "PP_ONLY", False, False),
]


@pytest.mark.parametrize("row", GOLDEN_V11, ids=lambda r: f"{r[0]}-{r[1]}")
def test_parity_with_research_v11(row):
    seed, day, status, codes, ratio, base_len, depth, strength, kind, wedge, spike = row
    result = pp.detect(_research_series(seed), signal_date=day,
                       params=pp.PocketPivotParams(base_min=25, pp5_max=4.5, optimized=False))
    m = result["metrics"]
    assert result["status"] == status
    assert sorted(result["reason_codes"]) == codes
    assert m["pp5_ratio_atr"] == pytest.approx(ratio, abs=1e-9)
    assert m["base_length"] == base_len
    assert m["base_depth"] == pytest.approx(depth, abs=1e-9)
    assert m["signature_strength"] == pytest.approx(strength, abs=1e-9)
    assert m["kind"] == kind
    assert ("RISING_WEDGE" in result["diag_codes"]) is wedge
    assert ("V_SPIKE" in result["diag_codes"]) is spike


def test_market_constants_tighten_the_book_defaults():
    # PP5 3.587 passes the book default 4.5 but not the selected 3.0
    loose = pp.detect(_research_series(0), signal_date="2021-01-27", market="US")
    assert loose["reason_codes"] == ["NOT_CONTRACTED_PRICE"]
    assert pp.detect(_research_series(2), signal_date="2021-03-03", market="US")["status"] == "PASS"


# --- ② continuation helper -------------------------------------------------------------------

def _trend_frame(close, open_=None, volume=None):
    close = np.asarray(close, dtype=float)
    open_ = close.copy() if open_ is None else np.asarray(open_, dtype=float)
    volume = np.full(len(close), 1_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.004,
                         "Low": np.minimum(open_, close) * 0.996, "Close": close, "Volume": volume},
                        index=pd.bdate_range("2025-01-01", periods=len(close)))


def _uptrend_close(n=80):
    """Steady advance with every third session a small pullback."""
    close = [100.0]
    for i in range(1, n):
        close.append(close[-1] * (0.996 if i % 3 == 0 else 1.006))
    return np.array(close)


def _uptrend(n=80):
    return _trend_frame(_uptrend_close(n))


def _continuation_day(frame, *, low_factor=0.985, volume=1_500_000.0, close_factor=1.004):
    prev = frame["Close"].iloc[-1]
    ma10_guess = frame["Close"].iloc[-9:].mean()
    return {"Open": prev, "High": prev * close_factor * 1.002,
            "Low": min(prev, ma10_guess) * low_factor, "Close": prev * close_factor, "Volume": volume}


def test_continuation_partial_bar_passes():
    frame = _uptrend()
    result = pp.continuation_pp(frame, today=_continuation_day(frame))
    assert result["status"] == "PASS", (result["reason_codes"], result["metrics"])
    assert result["metrics"]["partial_bar"] is True


def test_continuation_completed_last_bar_matches_partial():
    frame = _uptrend()
    today = _continuation_day(frame)
    completed = pd.concat([frame, pd.DataFrame([today], index=[frame.index[-1] + pd.offsets.BDay()])])
    a = pp.continuation_pp(frame, today=today)
    b = pp.continuation_pp(completed)
    assert a["status"] == b["status"] == "PASS"
    assert a["metrics"]["ma10"] == pytest.approx(b["metrics"]["ma10"])


@pytest.mark.parametrize("kwargs, code", [
    ({"low_factor": 1.02}, "NOT_AT_MA10"),
    ({"volume": 500_000.0}, "NO_VOLUME_SIGNATURE"),
    ({"close_factor": 0.999}, "NOT_UP_DAY"),
    ({"close_factor": 1.06}, "GAP_OR_SURGE"),
])
def test_continuation_counterexamples(kwargs, code):
    frame = _uptrend()
    result = pp.continuation_pp(frame, today=_continuation_day(frame, **kwargs))
    assert result["status"] == "FAIL"
    assert code in result["reason_codes"]


def test_continuation_rising_wedge_is_excluded():
    # last 20 sessions: price keeps rising while volume fades
    volume = np.full(80, 1_000_000.0)
    volume[-20:] = np.linspace(2_000_000, 600_000, 20)
    frame = _trend_frame(_uptrend_close(), volume=volume)
    result = pp.continuation_pp(frame, today=_continuation_day(frame, volume=1_900_000.0))
    assert "RISING_WEDGE" in result["reason_codes"]
    assert result["status"] == "FAIL"
    assert "RISING_WEDGE" not in pp.continuation_pp(_uptrend(), today=_continuation_day(_uptrend()))["reason_codes"]


def test_continuation_v_spike_is_excluded():
    c = _uptrend_close()
    base = c[-30]
    # +30% run to a peak, a gap down below the prior low, then a close above the peak
    c[-12:-6] = base * np.linspace(1.05, 1.30, 6)
    c[-6] = base * 1.24
    c[-5] = base * 1.31
    c[-4:] = base * np.array([1.30, 1.305, 1.31, 1.3])
    open_ = c.copy()
    open_[-6] = base * 1.20
    frame = _trend_frame(c, open_)
    result = pp.continuation_pp(frame, today=_continuation_day(frame))
    assert "V_SPIKE" in result["reason_codes"]
    assert "V_SPIKE" not in pp.continuation_pp(_uptrend(), today=_continuation_day(_uptrend()))["reason_codes"]


@pytest.mark.parametrize("bars, today, code", [
    (_uptrend(40), None, "SHORT_HISTORY"),
    (pd.DataFrame(), None, "NO_HISTORY"),
    (_uptrend(), {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": None, "Volume": 1.0}, "BAD_BAR"),
])
def test_continuation_missing(bars, today, code):
    result = pp.continuation_pp(bars, today=today)
    assert result["status"] == "MISSING"
    assert result["reason_codes"] == [code]


def test_metrics_are_finite_or_none():
    result = pp.detect(_clean_base(), market="US")
    for key, value in result["metrics"].items():
        if isinstance(value, float):
            assert math.isfinite(value), key
