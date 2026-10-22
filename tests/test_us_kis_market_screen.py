"""KIS condition-search market screen (#822): walk-down collection and batch shortlist.

The fake server reproduces what the production key showed on 2026-09-28: at
most 100 rows per call, sorted by market cap descending, continuation ignored,
``trec`` = matches in the requested cap band. No network, broker or channel.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "prism-us"), str(ROOT)]

from cores import kis_us_market_screen as screen_mod  # noqa: E402


class FakeKis:
    def __init__(self, listings, fail_on=None):
        # listings: {excd: [(symbol, valx_thousand, avol_usd), ...]}
        self.listings = listings
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, url, tr_id, params):
        assert url == screen_mod.SEARCH_URL and tr_id == screen_mod.SEARCH_TR_ID
        self.calls.append(dict(params))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            return SimpleNamespace(isOK=lambda: False, getErrorCode=lambda: "EGW00201")
        low, high = int(params["CO_ST_VALX"]), int(params["CO_EN_VALX"])
        band = [row for row in self.listings.get(params["EXCD"], []) if low <= row[1] <= high]
        band.sort(key=lambda row: -row[1])
        page = [{"symb": s, "valx": str(v), "avol": str(a), "last": "10.0", "popen": "9.5",
                 "phigh": "10.2", "plow": "9.4", "tvol": "1000", "rate": "+1.25", "e_ordyn": "○"}
                for s, v, a in band[:100]]
        body = SimpleNamespace(output1={"trec": str(len(band)), "crec": str(len(page))}, output2=page)
        return SimpleNamespace(isOK=lambda: True, getBody=lambda: body)


def _listing(prefix, count, top=5_000_000, step=10_000, amount=60_000_000):
    return [(f"{prefix}{i:03d}", top - i * step, amount) for i in range(count)]


def test_walk_down_collects_every_row_beyond_the_100_row_cap():
    fake = FakeKis({"NAS": _listing("N", 250), "NYS": _listing("Y", 30), "AMS": []})
    frame, diagnostic = screen_mod.fetch_market_screen(fake, 1e9, pause_seconds=0)
    assert len(frame) == 280
    assert diagnostic["status"] == "COMPLETE"
    assert diagnostic["exchanges"]["NAS"] == {"expected": 250, "collected": 250}
    assert diagnostic["calls"] == 3 + 1 + 1  # NAS 250 rows -> 3 pages, NYS 1, AMS 1
    assert frame.loc["N000", "MarketCap"] == 5_000_000 * 1000
    assert frame.loc["N000", "Amount"] == 60_000_000
    assert frame.loc["N000", "ChangeRate"] == 1.25
    assert bool(frame.loc["N000", "KisTradable"]) is True
    assert all(call["CO_ST_VALX"] == "1000000" for call in fake.calls)


def test_ties_at_the_page_boundary_are_not_lost_or_duplicated():
    rows = [(f"T{i:03d}", 2_000_000, 1) for i in range(40)]            # 40-way tie
    rows += [(f"H{i:03d}", 3_000_000 + i, 1) for i in range(80)]        # 80 above the tie
    frame, diagnostic = screen_mod.fetch_market_screen(FakeKis({"NAS": rows}), 1e9, pause_seconds=0)
    assert len(frame) == 120
    assert diagnostic["exchanges"]["NAS"]["collected"] == 120


def test_stalled_walk_raises_instead_of_returning_a_truncated_market():
    rows = [(f"S{i:03d}", 2_000_000, 1) for i in range(150)]            # 150 identical caps
    with pytest.raises(screen_mod.KisScreenError):
        screen_mod.fetch_market_screen(FakeKis({"NAS": rows}), 1e9, pause_seconds=0)


def test_api_error_and_call_cap_raise():
    with pytest.raises(screen_mod.KisScreenError):
        screen_mod.fetch_market_screen(FakeKis({"NAS": _listing("N", 5)}, fail_on=2), 1e9, pause_seconds=0)
    with pytest.raises(screen_mod.KisScreenError):
        screen_mod.fetch_market_screen(FakeKis({"NAS": _listing("N", 450)}), 1e9,
                                       max_calls=3, pause_seconds=0)


def test_class_share_symbols_match_the_directory_form():
    assert screen_mod.normalize_symbol("BRK/B") == "BRK-B"
    assert screen_mod.normalize_symbol("brk.b ") == "BRK-B"


# --- batch integration ------------------------------------------------------

def _batch():
    with patch("dotenv.load_dotenv", return_value=False):
        import us_trigger_batch as batch
    return batch


def _screen(amounts):
    frame = pd.DataFrame({"Amount": amounts}, dtype=float)
    frame["ChangeRate"] = 1.0
    return frame, {"status": "COMPLETE", "calls": 5}


def test_shortlist_keeps_names_near_the_floor_and_drops_the_rest(monkeypatch):
    batch = _batch()
    monkeypatch.delenv("US_SCREENING_KIS_SHORTLIST", raising=False)
    tickers = ["BIG", "NEAR", "LOW", "NOTKIS"]
    frame, diag = _screen({"BIG": 900e6, "NEAR": 16e6, "LOW": 10e6})
    with patch.object(batch, "_kis_request", return_value=object()), \
         patch.object(batch, "fetch_market_screen", return_value=(frame, diag)), \
         patch.object(batch, "get_major_tickers", return_value=["BIG"]):
        shortlist, screen, diagnostic = batch._kis_price_shortlist(tickers, 1e9)
    assert shortlist == ["BIG", "NEAR"]          # 16M >= 0.3 x 50M floor; 10M and non-KIS dropped
    assert diagnostic["status"] == "USED" and diagnostic["index_member_coverage"] == 1.0
    assert screen is frame


def test_screen_missing_index_members_is_rejected(monkeypatch):
    batch = _batch()
    monkeypatch.delenv("US_SCREENING_KIS_SHORTLIST", raising=False)
    frame, diag = _screen({"AAA": 900e6})
    with patch.object(batch, "_kis_request", return_value=object()), \
         patch.object(batch, "fetch_market_screen", return_value=(frame, diag)), \
         patch.object(batch, "get_major_tickers", return_value=["AAA", "AFL", "AJG"]):
        shortlist, screen, diagnostic = batch._kis_price_shortlist(["AAA", "AFL", "AJG"], 1e9)
    assert shortlist is None and screen is None
    assert diagnostic["status"] == "REJECTED"


def test_kis_failure_and_disable_switch_keep_full_collection(monkeypatch):
    batch = _batch()
    monkeypatch.delenv("US_SCREENING_KIS_SHORTLIST", raising=False)
    with patch.object(batch, "_kis_request", side_effect=RuntimeError("no config")):
        assert batch._kis_price_shortlist(["AAA"], 1e9)[2] == {"status": "UNAVAILABLE", "error_type": "RuntimeError"}
    monkeypatch.setenv("US_SCREENING_KIS_SHORTLIST", "false")
    assert batch._kis_price_shortlist(["AAA"], 1e9) == (None, None, {"status": "DISABLED"})


def test_load_inputs_prices_only_the_shortlist(monkeypatch):
    batch = _batch()
    from prism_core import us_stock_universe as universe
    monkeypatch.setenv("US_SCREENING_UNIVERSE", "listed_common")
    monkeypatch.delenv("US_SCREENING_MIN_MARKET_CAP_USD", raising=False)
    symbols = ["AAA", "BBB", "CCC"]
    records = [universe.UniverseRecord(s, s + " Common Stock", "NASDAQ") for s in symbols]
    frame, _ = _screen({"AAA": 200e6, "CCC": 5e6})
    priced = {}

    def pair(trade_date, tickers):
        priced["tickers"] = list(tickers)
        snap = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5,
                             "Volume": 1e7, "Amount": 105e6}, index=list(tickers))
        return snap, snap.copy(), "20260911", {"download_invocations": 1}

    info = {"quoteType": "EQUITY", "marketCap": 5e9, "currency": "USD", "exchange": "NMS",
            "sector": "Technology", "industry": "Software", "shortName": "AAA", "longName": "AAA Inc."}
    with patch.object(universe, "fetch_universe", return_value=universe.UniverseResult(records, {"fixture": 3})), \
         patch.object(batch, "_kis_price_shortlist",
                      return_value=(["AAA"], frame, {"status": "USED", "shortlist_count": 1})), \
         patch.object(batch, "get_batched_snapshot_pair", side_effect=pair), \
         patch("yfinance.Ticker", return_value=SimpleNamespace(info=info)), \
         patch("prism_core.market_intelligence.enabled", return_value=True):
        tickers, current, previous, date, diagnostic = batch._load_screening_inputs("20260914")
    assert priced["tickers"] == ["AAA"]
    assert tickers == ["AAA"] and list(current.index) == ["AAA"]
    assert diagnostic["kis_market_screen"]["status"] == "USED"
    assert diagnostic["eligible_count"] == 1
    participation = diagnostic["market_participation"]
    assert participation["universe_count"] == 2              # AAA + CCC listed on KIS, not only priced names
    assert participation["advance"] == 2


# --- previous-session fallback (2026-10-22: yfinance dropped the 10/21 bar for 581/583 names) ---

class FakeDaily:
    def __init__(self, bars, fail=()):
        # bars: {symbol: [(xymd, open, high, low, close, volume), ...]}
        self.bars, self.fail, self.calls = bars, set(fail), []

    def __call__(self, url, tr_id, params):
        assert url == screen_mod.DAILY_URL and tr_id == screen_mod.DAILY_TR_ID
        assert params["GUBN"] == "0" and params["MODP"] == "1"
        self.calls.append(dict(params))
        if params["SYMB"] in self.fail:
            return SimpleNamespace(isOK=lambda: False, getErrorCode=lambda: "EGW00201")
        page = [{"xymd": d, "open": str(o), "high": str(h), "low": str(lo), "clos": str(c), "tvol": str(v)}
                for d, o, h, lo, c, v in self.bars.get(params["SYMB"], [])]
        return SimpleNamespace(isOK=lambda: True, getBody=lambda: SimpleNamespace(output2=page))


def test_exact_daily_bars_accept_only_the_requested_session():
    fake = FakeDaily({
        "AAPL": [("20261021", 339.36, 345.25, 338.83, 343.99, 35614126),
                 ("20261020", 339.86, 343.06, 338.7, 339.5, 35851382)],
        "OLD": [("20261020", 10, 11, 9, 10.5, 1000)],          # target session absent
        "BAD": [("20261021", 10, 11, 9, "", 1000)],             # blank close
    }, fail=("ERR",))
    frame, diag = screen_mod.fetch_exact_daily_bars(
        fake, {"AAPL": "NAS", "OLD": "NYS", "BAD": "NYS", "ERR": "NAS", "BRK-B": "NYS"},
        "20261021", pause_seconds=0)
    assert list(frame.index) == ["AAPL"]
    assert frame.loc["AAPL", "Close"] == 343.99 and frame.loc["AAPL", "Volume"] == 35614126
    assert frame.loc["AAPL", "Amount"] == pytest.approx(343.99 * 35614126)
    assert diag["reason_counts"] == {"missing_exact_date": 1, "invalid_ohlcv": 1,
                                     "request_failed": 1, "unsupported_symbol": 1}
    assert [c["EXCD"] for c in fake.calls] == ["NAS", "NYS", "NYS", "NAS"]


def test_exact_daily_bars_stop_after_consecutive_failures():
    fake = FakeDaily({}, fail=("A", "B", "C"))
    frame, diag = screen_mod.fetch_exact_daily_bars(
        fake, {s: "NAS" for s in ("A", "B", "C", "D", "E")}, "20261021",
        pause_seconds=0, failure_stop=3)
    assert frame.empty and len(fake.calls) == 3
    assert diag["stop_reason"] == "failure_stop"
    assert diag["reason_counts"] == {"request_failed": 3, "failure_stop": 2}


def _snap(tickers, close=10.0):
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": 1e7, "Amount": close * 1e7}, index=list(tickers))


def test_previous_fallback_fills_only_missing_names_and_updates_coverage(monkeypatch):
    batch = _batch()
    monkeypatch.delenv("US_SNAPSHOT_KIS_DAILY_FALLBACK", raising=False)
    current = _snap(["AAA", "BBB", "CCC"])
    previous = _snap(["AAA"], close=9.0)
    previous.attrs["snapshot_coverage"] = {"requested_count": 3, "valid_count": 1,
                                           "missing_count": 2, "status": "PARTIAL",
                                           "reason_counts": {"missing_exact_date": 2}}
    screen = pd.DataFrame({"KisExchange": ["NAS", "NYS", "NAS"]}, index=["AAA", "BBB", "CCC"])
    fake = FakeDaily({"BBB": [("20261021", 8, 9, 7, 8.5, 2000)]})
    with patch.object(batch, "_kis_request", return_value=fake):
        filled, diag = batch._fill_previous_from_kis("20261021", ["AAA", "BBB", "CCC"],
                                                     current, previous, screen)
    assert [c["SYMB"] for c in fake.calls] == ["BBB", "CCC"]     # AAA already had a provider bar
    assert sorted(filled.index) == ["AAA", "BBB"]
    assert filled.loc["AAA", "Close"] == 9.0 and filled.loc["BBB", "Close"] == 8.5
    coverage = filled.attrs["snapshot_coverage"]
    assert coverage["valid_count"] == 2 and coverage["missing_count"] == 1
    assert coverage["kis_daily_fallback_count"] == 1
    assert coverage["reason_counts"] == {"missing_exact_date": 2}  # provider reasons kept
    assert diag["status"] == "USED" and diag["filled_count"] == 1


def test_previous_fallback_is_skipped_without_exchange_map_or_when_disabled(monkeypatch):
    batch = _batch()
    current, previous = _snap(["AAA"]), _snap([])
    with patch.object(batch, "_kis_request", side_effect=AssertionError("no KIS call")):
        assert batch._fill_previous_from_kis("20261021", ["AAA"], current, previous, None)[1] == \
            {"status": "NO_EXCHANGE_MAP"}
        monkeypatch.setenv("US_SNAPSHOT_KIS_DAILY_FALLBACK", "false")
        screen = pd.DataFrame({"KisExchange": ["NAS"]}, index=["AAA"])
        assert batch._fill_previous_from_kis("20261021", ["AAA"], current, previous, screen)[1] == \
            {"status": "DISABLED"}
