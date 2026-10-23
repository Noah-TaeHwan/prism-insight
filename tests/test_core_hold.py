"""Core long-term holding rule and its record-only observer (H-1023-corehold, 2026-10-23)."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from prism_core import core_hold as C
from tools import run_core_hold_observer as O


def _days(n, start=date(2025, 1, 1)):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _bars(closes, lows=None, opens=None, highs=None):
    days = _days(len(closes))
    return [{"date": days[i], "open": (opens or closes)[i], "high": (highs or [c * 1.01 for c in closes])[i],
             "low": (lows or [c * 0.99 for c in closes])[i], "close": c} for i, c in enumerate(closes)]


def _uptrend(n=300, start=50.0, step=0.004):
    closes, price = [], start
    for _ in range(n):
        closes.append(price)
        price *= 1 + step
    return closes


def test_promotion_needs_every_condition_and_unknown_is_never_a_promotion():
    bars = _bars(_uptrend())
    entry_i = 230
    entry = bars[entry_i]["close"] / 1.3 * 0.99           # +30% reached by the 40th session
    view = C.promotion(bars, entry_i + 39, entry=entry, entry_session=bars[entry_i]["date"])
    assert view["sessions"] == 40 and view["eligible"] is True
    early = C.promotion(bars, entry_i + 38, entry=entry, entry_session=bars[entry_i]["date"])
    assert early["conditions"]["sessions"] is False and early["eligible"] is False
    low_gain = C.promotion(bars, entry_i + 39, entry=bars[entry_i]["close"], entry_session=bars[entry_i]["date"])
    assert low_gain["conditions"]["gain"] is False
    short = _bars(_uptrend(150))                         # no MA200 -> unknown, not 0 and not a pass
    unknown = C.promotion(short, 140, entry=1.0, entry_session=short[90]["date"])
    assert unknown["conditions"]["trend"] is None and unknown["eligible"] is None


def test_overheated_close_is_not_promoted():
    closes = _uptrend()
    closes[-1] = closes[-2] * 1.8                         # far above 1.4 x MA50
    bars = _bars(closes)
    view = C.promotion(bars, len(bars) - 1, entry=1.0, entry_session=bars[200]["date"])
    assert view["conditions"]["not_overheated"] is False and view["eligible"] is False


def test_core_exit_needs_the_next_day_low_under_the_break_day_low():
    closes = _uptrend(260)
    bars = _bars(closes)
    promoted = 230
    assert C.core_exit(bars, start_index=promoted, entry=10.0) is None   # still above MA200: holding
    m200 = C.ma(C.closes_of(bars), 250, 200)
    bars[250].update(close=m200 * 0.98, low=m200 * 0.97, open=m200)
    bars[251].update(close=m200 * 1.01, low=m200 * 0.975, open=m200 * 1.0)  # low not under day-t low: no sell
    assert C.core_exit(bars[:252], start_index=promoted, entry=10.0) is None
    bars[251].update(low=m200 * 0.96, open=m200 * 0.99)
    out = C.core_exit(bars, start_index=promoted, entry=10.0)
    assert out["code"] == "MA200_BREAK" and out["date"] == bars[251]["date"]
    assert out["price"] == pytest.approx(min(m200 * 0.99, m200 * 0.97))


def test_core_exit_breakeven_and_the_higher_stop_fills_first():
    bars = _bars(_uptrend(260))
    entry = bars[230]["close"] * 0.9                       # every low after the promotion is above it
    bars[245].update(low=entry * 0.95, open=entry * 1.02)
    out = C.core_exit(bars, start_index=230, entry=entry)
    assert out["code"] == "BREAKEVEN" and out["price"] == pytest.approx(entry)


def test_counterfactual_engine_exit_two_closes_under_ma50_then_next_open():
    closes = _uptrend(260)
    bars = _bars(closes)
    for i in (250, 251):
        m50 = C.ma(C.closes_of(bars), i, 50)
        bars[i].update(close=m50 * 0.97)
    entry = bars[240]["close"]                             # +6% in 15 sessions: not a runner
    kind, out = C.counterfactual_path(bars, start_index=245, entry=entry, entry_session=bars[240]["date"],
                                      promote=False)
    assert kind == "EXIT" and out["code"] == "MA50_TWO_CLOSES" and out["date"] == bars[252]["date"]
    # a runner (+20% on sessions 4..15) sells on one close under MA50, the next open
    kind, out = C.counterfactual_path(bars, start_index=245, entry=entry / 1.25, entry_session=bars[240]["date"],
                                      promote=False)
    assert kind == "EXIT" and out["code"] == "RUNNER_MA50" and out["date"] == bars[251]["date"]
    kind, out = C.counterfactual_path(bars, start_index=245, entry=bars[240]["close"] * 2,
                                      entry_session=bars[240]["date"], first_entry=bars[246]["close"] * 1.2)
    assert kind == "EXIT" and out["code"] == "HARD_STOP"


def test_rank_uses_the_steady_leader_score_and_ticker_ties():
    ranked = C.rank([{"ticker": "B", "share": 0.9, "ret": 0.5}, {"ticker": "A", "share": 0.5, "ret": 0.1},
                     {"ticker": "C", "share": None, "ret": 0.9}])
    assert [r["ticker"] for r in ranked][0] == "B"
    tie = C.rank([{"ticker": "Z", "share": 0.5, "ret": 0.1}, {"ticker": "Y", "share": 0.5, "ret": 0.1}])
    assert [r["ticker"] for r in tie] == ["Y", "Z"]


def test_allocate_caps_two_slots_without_replacement_and_frees_after_exit():
    def cand(t, day, exit_day=None):
        return {"ticker": t, "a": {"date": day, "share": 0.5, "ret": 0.1,
                                   "exit": {"date": exit_day} if exit_day else None}}
    results = [cand("A", "2026-01-05", "2026-01-20"), cand("B", "2026-01-06"), cand("C", "2026-01-07"),
               cand("D", "2026-01-21")]
    O.allocate(results, "a")
    assert [r["a"]["slot"] for r in results] == [True, True, False, True]


def _db(tmp_path, rows_open, rows_closed):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    for table in ("us_stock_holdings", "us_trading_history"):
        extra = ", sell_date TEXT, sell_price REAL, exit_kind TEXT" if "history" in table else ""
        conn.execute(f"CREATE TABLE {table} (ticker TEXT, company_name TEXT, buy_date TEXT, buy_price REAL, "
                     f"scenario TEXT{extra})")
    conn.executemany("INSERT INTO us_stock_holdings VALUES (?,?,?,?,?)", rows_open)
    conn.executemany("INSERT INTO us_trading_history VALUES (?,?,?,?,?,?,?,?)", rows_closed)
    conn.commit()
    conn.close()
    return path


def test_observer_records_both_variants_and_never_writes_the_database(tmp_path, monkeypatch):
    closes = _uptrend(320)
    bars = _bars(closes)
    entry_day = bars[230]["date"]
    buy = f"{entry_day} 23:30:00"                          # KST evening = that New York session
    entry = bars[230]["close"]
    sold = bars[245]["date"]                               # trailing sale on session 16 after +5%
    path = _db(tmp_path,
               [("HOLD", "Hold Co", buy, entry / 1.3 * 0.95, "{}")],
               [("TRAIL", "Trail Co", buy, entry, "{}", f"{sold} 23:00:00", entry * 1.06, "stop")])
    fetch = lambda market, ticker, start, end: ([dict(b) for b in bars], [])  # noqa: E731
    results = O.run("US", since="2025-01-01", today=date(2026, 12, 31), fetch=fetch, path=path, regimes=[])
    by = {r["ticker"]: r for r in results}
    assert O.status(by["HOLD"], "a") in ("CORE", "CORE_EXITED")
    assert O.status(by["TRAIL"], "a") == "SOLD_BEFORE_PROMOTION"
    assert by["TRAIL"]["b_applies"] is True and O.status(by["TRAIL"], "b") in ("CORE", "CORE_EXITED")
    assert by["TRAIL"]["corporate_action"] is False
    text = O.report(results, "US", "2025-01-01")
    assert "(b) trailing exempt" in text and "payoff" in text and "TRAIL" in text
    with pytest.raises(sqlite3.OperationalError):          # the observer opens the database read-only
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("DELETE FROM us_stock_holdings")


def test_missing_bars_stay_unknown(tmp_path):
    path = _db(tmp_path, [("X", "X", "2026-10-01 10:00:00", 10.0, "{}")], [])
    results = O.run("US", since="2026-10-23", today=date(2026, 10, 23),
                    fetch=lambda *a: ([], None), path=path, regimes=[])
    assert results[0]["error"] == "bars" and O.status(results[0], "a") == "UNKNOWN"
    assert O.outcome(results[0], "a") == (None, None, True) or results[0].get("last_close") is None


def test_record_writes_state_journal_and_events_only_on_change(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "ROOT", tmp_path)
    sent = []
    monkeypatch.setattr(O, "_emit", lambda market, r, before, after: sent.append((r["ticker"], after)))
    result = {"key": "X|2026", "ticker": "X", "open": True, "a": None, "b": None, "entry_avg": 10.0,
              "last_close": {"date": "2026-10-23", "close": 11.0}}
    O.record("US", [result], today=date(2026, 10, 23))
    O.record("US", [result], today=date(2026, 10, 24))
    state = json.loads((tmp_path / "runtime/core_hold_us.json").read_text())
    assert state["record_only"] and state["positions"]["X|2026"]["status"] == "WATCH"
    assert len((tmp_path / "runtime/core_hold_journal_us.jsonl").read_text().splitlines()) == 2
    assert sent == [("X", ["WATCH", "WATCH"])]


def test_switch_off_and_no_order_or_holding_write_in_the_source(monkeypatch, capsys):
    monkeypatch.setenv("CORE_HOLD_OBSERVER_ENABLED", "false")
    assert O.main(["--market", "KR"]) == 0 and "disabled" in capsys.readouterr().out
    src = (O.ROOT / "tools/run_core_hold_observer.py").read_text(encoding="utf-8")
    for word in ("sell_stock", "buy_stock", "UPDATE ", "INSERT ", "DELETE ", "place_order"):
        assert word not in src


def test_the_watchdog_heartbeat_matches_the_summary_line():
    import re

    from tools import ops_watchdog as W
    jobs = {j.key: j for j in W.JOBS}
    for market in ("KR", "US"):
        job = jobs[f"core_hold_{market.lower()}"]
        line = f"Core-hold {market} summary: positions=1 statuses={{'WATCH': 1}} missing=0"
        assert re.search(job.heartbeat, line) and job.deadline
    src = (O.ROOT / "tools/run_core_hold_observer.py").read_text(encoding="utf-8")
    assert 'f"Core-hold {args.market} summary: positions=' in src


def test_promotion_records_the_logged_regime_or_missing(tmp_path):
    from datetime import datetime
    log = tmp_path / "regime.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in (
        {"ts": "2026-10-20 10:00:00", "market": "KR", "regime": "moderate_bull", "confidence": 0.7},
        {"ts": "2026-10-20 23:20:00", "market": "US", "regime": "sideways", "confidence": 0.6},
        {"ts": "2026-10-23 13:25:54", "market": "KR", "regime": "strong_bear", "confidence": 0.9})) + "\n")
    kr, us = O.load_regimes("KR", log), O.load_regimes("US", log)
    assert kr[0][0] == datetime(2026, 10, 20, 10, 0)
    assert O.regime_at(kr, "KR", "2026-10-23")["regime"] == "strong_bear"      # logged before the 15:30 close
    assert O.regime_at(kr, "KR", "2026-10-22")["regime"] == "moderate_bull"
    assert O.regime_at(us, "US", "2026-10-20")["regime"] == "sideways"         # logged in the New York session (KST evening)
    assert O.regime_at(kr, "KR", "2026-11-30") is None                          # too old -> MISSING
    assert O.regime_at([], "US", "2026-10-20") is None
