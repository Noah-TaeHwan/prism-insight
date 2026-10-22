"""T-B "Steady Leader Pullback" detector, ranking and live merge (prism_core.steady_leader*).

The detector must reproduce the preregistered research definition (tasks/research-20261022/
new-triggers, newtrig.py ``TB``): checked against a port of the research expressions on random
series and against the research's KO 2026 signal dates on real KO daily bars.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prism_core import steady_leader as sl
from prism_core import steady_leader_trigger as slt

FIXTURE = Path(__file__).parent / "fixtures" / "steady_leader" / "ko_daily_yfinance.csv"
# Research output ko_tb_2026.json (newtrig.py on the yfinance cache, 2026-10-22).
KO_RESEARCH_2026 = ["2026-01-20", "2026-01-21", "2026-02-27", "2026-05-15", "2026-05-19",
                    "2026-06-26", "2026-09-18", "2026-10-16"]


def steady_bars(n=300, *, end="2026-09-16", pullback=True, signal_volume=1.6e6, gap=0.004,
                change=0.02, scale=1.0, growth=2.0):
    """Steady uptrend (≈ +0.23%/session with a small zigzag), a 4-session dip to the 20-day line,
    then an up day on ``signal_volume`` against 1.0M on down days."""
    base = 50.0 * np.exp(np.linspace(0.0, np.log(growth), n))
    close = base * np.where(np.arange(n) % 2 == 0, 1.003, 0.997)
    if pullback:
        peak = close[n - 6]
        close[n - 5:n - 1] = peak * np.array([0.992, 0.982, 0.975, 0.972])
    close[n - 1] = close[n - 2] * (1 + change)
    open_ = np.r_[close[0], close[:-1]] * 1.0005
    open_[-1] = close[-2] * (1 + gap)
    high = np.maximum(open_, close) * 1.006
    low = np.minimum(open_, close) * 0.994
    vol = np.full(n, 1.0e6)
    vol[-1] = signal_volume
    frame = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
                         index=pd.bdate_range(end=end, periods=n))
    return frame * np.array([scale, scale, scale, scale, 1.0])


def research_tb(frame: pd.DataFrame) -> pd.Series:
    """newtrig.py TB mask for one series, liquidity/cap handled by the batch universe."""
    C, O, H, L, V = (frame[[k]].rename(columns={k: "x"}) for k in ("Close", "Open", "High", "Low", "Volume"))
    PC = C.shift(1)
    TR = pd.DataFrame(np.fmax(np.fmax((H - L).to_numpy(), (H - PC).abs().to_numpy()), (L - PC).abs().to_numpy()),
                      index=C.index, columns=C.columns)
    ATRP = TR.rolling(20, min_periods=1).mean().shift(1) / PC
    UP, DOWN = C > PC, C < PC
    DV = V.where(DOWN)
    MX10 = DV.shift(1).rolling(10, min_periods=1).max()
    PP2B = MX10.notna() & (V > MX10)
    MA10, MA20, MA50 = C.rolling(10).mean(), C.rolling(20).mean(), C.rolling(50).mean()
    MA200 = C.rolling(200, min_periods=180).mean()
    GAP = (O / PC - 1 >= 2 * ATRP) | (C / PC - 1 >= 3 * ATRP)
    HI252 = C.shift(1).rolling(252, min_periods=200).max()
    NH = C.notna().cumsum()
    pull = ((L <= MA20 * 1.01) | (L <= MA10)).astype(float).shift(1).rolling(5, min_periods=5).max() == 1
    TB = ((NH > 200) & (C > MA50) & (MA50 > MA200) & (MA50 > MA50.shift(10)) & (MA200 > MA200.shift(20))
          & (C >= 0.90 * HI252) & pull & UP & (C > MA10) & PP2B & ~GAP)
    return TB["x"]


# --- definition fidelity ---------------------------------------------------------------------

def test_ko_2026_signal_dates_match_research():
    ko = pd.read_csv(FIXTURE, index_col=0, parse_dates=True)
    days = ko.index[ko.index >= "2026-01-01"]
    hits = [d.strftime("%Y-%m-%d") for d in days if sl.detect(ko.loc[:d])["status"] == "PASS"]
    assert hits == KO_RESEARCH_2026
    # same answer through signal_date on the full frame (no later bar can leak in)
    assert sl.detect(ko, signal_date="2026-09-18")["status"] == "PASS"
    assert sl.detect(ko, signal_date="2026-09-17")["status"] == "FAIL"


@pytest.mark.parametrize("seed", range(6))
def test_matches_research_expressions_on_random_series(seed):
    rng = np.random.default_rng(seed)
    n = 420
    ret = rng.normal(0.0012, 0.012, n)
    close = 40.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.012, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.012, n))
    vol = rng.uniform(0.6e6, 1.8e6, n)
    frame = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
                         index=pd.bdate_range(end="2026-09-16", periods=n))
    expected = research_tb(frame)
    days = frame.index[195:]
    got = pd.Series([sl.detect(frame.loc[:d])["status"] == "PASS" for d in days], index=days)
    assert got.tolist() == expected.loc[days].tolist()
    assert expected.loc[days].any()                         # each seed has signal days
    statuses = {sl.detect(frame.loc[:d])["status"] for d in frame.index[:200]}
    assert statuses == {"MISSING"}


# --- examples and counterexamples ------------------------------------------------------------

def test_valid_steady_pullback_fires():
    result = sl.detect(steady_bars())
    assert result["status"] == "PASS", result["reason_codes"]
    m = result["metrics"]
    assert m["steadiness"] == 1.0 and m["ret_120"] > 0.2
    assert m["pullback_date"] is not None and m["signature_strength"] == pytest.approx(1.6)
    assert m["structural_stop"] == pytest.approx(m["ma20"] * 0.99)
    assert m["structural_stop"] < m["close"]


def test_ko_like_low_volatility_leader_fires_without_rs():
    """KO type: slow, low-volatility climb (about +12%/year) that RS rankings miss."""
    n = 320
    base = 60.0 * np.exp(np.linspace(0.0, np.log(1.18), n))
    close = base * np.where(np.arange(n) % 2 == 0, 1.002, 0.998)
    close[n - 5:n - 1] = close[n - 6] * np.array([0.996, 0.992, 0.99, 0.989])
    close[n - 1] = close[n - 2] * 1.008
    open_ = np.r_[close[0], close[:-1]] * 1.0003
    frame = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.003,
                          "Low": np.minimum(open_, close) * 0.997, "Close": close,
                          "Volume": np.r_[np.full(n - 1, 12e6), 15e6]},
                         index=pd.bdate_range(end="2026-09-18", periods=n))
    result = sl.detect(frame)
    assert result["status"] == "PASS", result["reason_codes"]
    assert result["metrics"]["ret_120"] < 0.10      # a weak 120-day return is no gate


def test_gap_or_surge_does_not_fire():
    gap = sl.detect(steady_bars(gap=0.06, change=0.065))
    assert gap["status"] == "FAIL" and "GAP_OR_SURGE" in gap["reason_codes"]


def test_trend_without_pullback_does_not_fire():
    # steeper climb: the zigzag lows stay above the 10- and 20-day lines
    result = sl.detect(steady_bars(pullback=False, change=0.01, growth=4.0))
    assert result["status"] == "FAIL"
    assert result["reason_codes"] == ["NO_PULLBACK"]


def test_volume_signature_needs_a_down_day_in_ten_sessions():
    weak = sl.detect(steady_bars(signal_volume=0.9e6))
    assert weak["reason_codes"] == ["NO_VOLUME_SIGNATURE"]
    frame = steady_bars()
    close = frame["Close"].to_numpy().copy()
    close[-11:-1] = close[-12] * np.cumprod(np.full(10, 1.001))   # no down day in d-10..d-1
    close[-1] = close[-2] * 1.01
    frame["Close"] = close
    result = sl.detect(frame)
    assert "NO_DOWN_DAY" in result["reason_codes"] and result["status"] == "FAIL"


def test_downtrend_and_far_from_high_fail():
    frame = steady_bars()
    frame = frame.iloc[::-1].set_axis(frame.index)          # same bars, falling
    result = sl.detect(frame)
    assert result["status"] == "FAIL"
    assert {"BELOW_MA50", "MA50_BELOW_MA200", "MA50_NOT_RISING"} <= set(result["reason_codes"])


@pytest.mark.parametrize("frame, code", [
    (pd.DataFrame(), "NO_HISTORY"),
    (steady_bars().drop(columns=["Volume"]), "BAD_COLUMNS"),
    (steady_bars().iloc[-200:], "SHORT_HISTORY"),
])
def test_unknown_inputs_are_missing(frame, code):
    result = sl.detect(frame)
    assert result["status"] == "MISSING" and result["reason_codes"] == [code]


def test_signal_date_without_bar_is_missing():
    assert sl.detect(steady_bars(end="2026-09-16"), signal_date="2026-09-19")["reason_codes"] == ["NO_BAR"]


def test_rank_is_percentile_within_candidates_and_keeps_two():
    rows = [{"ticker": "A", "steadiness": 0.9, "ret_120": 0.10},
            {"ticker": "B", "steadiness": 1.0, "ret_120": 0.30},
            {"ticker": "C", "steadiness": 0.8, "ret_120": 0.50}]
    ranked = sl.rank_candidates(rows)
    assert [r["ticker"] for r in ranked] == ["B", "C", "A"]
    assert ranked[0]["rank_score"] == pytest.approx(0.5 * 1.0 + 0.5 * (2 / 3))
    assert [r["top"] for r in ranked] == [True, True, False]
    assert sl.rank_candidates([]) == []


# --- live merge: at most one pick, regime cap stays hard ------------------------------------

def _frame(tickers, channel="bottom-up", scores=None):
    return pd.DataFrame({"Close": [10.0] * len(tickers), "final_score": scores or [0.5] * len(tickers),
                         "SelectionChannel": [channel] * len(tickers)}, index=tickers)


def _block(*tickers):
    candidates = [{"ticker": t, "rank": i + 1, "top": i < 2, "rank_score": 1 - i / 10}
                  for i, t in enumerate(tickers)]
    return {"trigger_key": "꾸준 상승 주도주 눌림", "candidates": candidates,
            "top_tickers": [c["ticker"] for c in candidates if c["top"]]}


def _pick(ticker, candidate):
    return pd.DataFrame({"Close": [20.0]}, index=[ticker])


def _total(result):
    return sum(len(f.index) for f in result.values())


def test_live_adds_into_free_slot():
    final = {"T1": _frame(["A"]), "T2": _frame(["B"])}
    merged, decision = slt.merge_live(final, _block("X", "Y"), max_selections=3, pick_frame=_pick,
                                      score_column="final_score")
    assert decision["reason"] == "ADDED" and decision["selected_ticker"] == "X"
    assert list(merged["꾸준 상승 주도주 눌림"].index) == ["X"] and _total(merged) == 3
    assert merged["꾸준 상승 주도주 눌림"]["SelectionChannel"].iloc[0] == "bottom-up"
    assert list(final) == ["T1", "T2"]                      # input not mutated


def test_live_replaces_only_the_lowest_bottom_up_pick_when_full():
    final = {"T1": _frame(["A"], "top-down", [0.1]), "T2": _frame(["B", "C"], scores=[0.9, 0.2])}
    merged, decision = slt.merge_live(final, _block("X"), max_selections=3, pick_frame=_pick,
                                      score_column="final_score")
    assert decision["reason"] == "REPLACED_LOWEST_BOTTOM_UP"
    assert decision["displaced_ticker"] == "C" and decision["displaced_trigger"] == "T2"
    assert _total(merged) == 3 and "A" in merged["T1"].index      # top-down kept
    assert list(merged["T2"].index) == ["B"]


@pytest.mark.parametrize("cap, final, reason", [
    (0, {}, "REGIME_CAP_ZERO"),
    (1, {"T1": _frame(["A"])}, "SINGLE_SLOT_CAP_FULL"),             # post-FTD pilot cap
    (2, {"T1": _frame(["A", "B"], "top-down")}, "NO_BOTTOM_UP_TO_REPLACE"),
])
def test_live_never_breaks_the_hard_cap(cap, final, reason):
    merged, decision = slt.merge_live(final, _block("X"), max_selections=cap, pick_frame=_pick,
                                      score_column="final_score")
    assert decision["applied"] is False and decision["reason"] == reason
    assert merged == final


def test_live_single_slot_cap_takes_an_empty_slot():
    merged, decision = slt.merge_live({}, _block("X"), max_selections=1, pick_frame=_pick,
                                      score_column="final_score")
    assert decision["applied"] and _total(merged) == 1


def test_live_skips_already_selected_and_unpriced_then_stops_at_top_two():
    final = {"T1": _frame(["X"])}
    merged, decision = slt.merge_live(final, _block("X", "Y", "Z"), max_selections=3,
                                      pick_frame=lambda t, c: None if t == "Y" else _pick(t, c),
                                      score_column="final_score")
    assert decision["applied"] is False and decision["reason"] == "TOP_ALREADY_SELECTED_OR_UNPRICED"
    merged, decision = slt.merge_live(final, _block("X", "Y", "Z"), max_selections=3,
                                      pick_frame=_pick, score_column="final_score")
    assert decision["selected_ticker"] == "Y" and _total(merged) == 2


def test_unmark_displaced_clears_only_that_row():
    rows = [{"ticker": "A", "selected": 1, "selected_trigger": "T", "selection_channel": "bottom-up"},
            {"ticker": "B", "selected": 1, "selected_trigger": "T", "selection_channel": "bottom-up"}]
    slt.unmark_displaced(rows, "B")
    assert rows[0]["selected"] == 1 and rows[1] == {"ticker": "B", "selected": 0,
                                                    "selected_trigger": None, "selection_channel": None}


def test_mode_resolution(monkeypatch):
    for raw, expected in (("", "off"), ("off", "off"), (" Observe ", "observe"), ("live", "live"),
                          ("on", "off"), ("true", "off")):
        monkeypatch.setenv("STEADY_LEADER_TRIGGER_MODE", raw)
        assert slt.resolve_mode() == expected
    monkeypatch.delenv("STEADY_LEADER_TRIGGER_MODE")
    assert slt.resolve_mode() == "off"
