"""Staged trust + core long-term holding LIVE wiring behind CORE_HOLD_LIVE_ENABLED (default off)."""
import json
import sqlite3
from pathlib import Path

import pytest

from prism_core import core_hold as C
from prism_core import core_hold_live as L
from prism_core import runner_hold_live as RL

ROOT = Path(__file__).resolve().parents[1]
BLOCK = {"version": L.LIVE_VERSION, "status": L.CORE, "promoted_on": "2026-12-01", "entry_first": 100.0,
         "last": {"date": "2026-12-02", "ma200": 110.0, "armed": False}}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("CORE_HOLD_LIVE_ENABLED", "true")
    monkeypatch.delenv("CORE_HOLD_LIVE_MARKETS", raising=False)


def _stock(price=150.0, stop=100.0, block=BLOCK, **extra):
    return {"id": 1, "ticker": "AAA", "buy_price": 100.0, "current_price": price, "stop_loss": stop,
            "scenario": json.dumps({C.SCENARIO_KEY: block} if block else {}), **extra}


def _bars(closes, lows=None):
    return [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "open": c, "high": c * 1.01,
             "low": (lows or {}).get(i, c * 0.99), "close": c} for i, c in enumerate(closes)]


def test_off_by_default_and_per_market(monkeypatch):
    monkeypatch.delenv("CORE_HOLD_LIVE_ENABLED", raising=False)
    assert not L.enabled("KR") and L.view(_stock(), "KR") is None and L.exit_kind(_stock(), "KR") is None
    assert L.prompt_block(_stock(), market="KR", language="ko") == ""
    monkeypatch.setenv("CORE_HOLD_LIVE_ENABLED", "true")
    monkeypatch.setenv("CORE_HOLD_LIVE_MARKETS", "US")
    assert L.enabled("US") and not L.enabled("KR")


def test_view_and_exit_kind_only_for_a_live_core_block(live):
    assert L.view(_stock(), "US")["phase"] == L.CORE and L.exit_kind(_stock(), "US") == "core_exit"
    assert L.view(_stock(block=None), "US") is None
    assert L.view(_stock(block=dict(BLOCK, version=C.VERSION)), "US") is None      # a record-only block never counts
    assert L.view(_stock(block=dict(BLOCK, status="EXITED")), "US") is None


def test_stop_is_the_first_buy_until_a_close_below_the_200_day_line():
    closes = [100.0 + i * 0.5 for i in range(220)]
    stop, facts = L.stop_target(_bars(closes), 90.0)
    assert stop == 90.0 and facts["armed"] is False
    broken = closes[:-1] + [C.ma(closes, 218, 200) * 0.97]
    stop, facts = L.stop_target(_bars(broken, lows={219: 150.0}), 90.0)
    assert facts["armed"] is True and stop == 150.0                  # that day's low, above the first buy
    stop, facts = L.stop_target(_bars([100.0] * 50), 90.0)           # MA200 unknown: keep breakeven, no guess
    assert stop == 90.0 and facts["armed"] is None
    assert L.stop_target([], 90.0) == (None, {})


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_block_states_the_same_rule_in_both_languages(live, language):
    text = L.prompt_block(_stock(), market="US", language=language)
    if language == "ko":
        assert "장기 보유(코어) 규칙" in text and "200일선" in text and "(3) 공식 확인된 법인 이벤트" in text
        assert "3단계 추세 약화" in text and "trailing stop 이탈" in text and "needed=false" in text
    else:
        assert "Core long-term holding rule" in text and "200-day" in text and "(3) an officially confirmed" in text
        assert "Tier-3 trend-weakness" in text and "trailing-stop break" in text and "needed=false" in text
    assert "next_session_add_plan" in text and "+50.0%" in text


def test_runner_prompt_hook_returns_the_core_block_first(live, monkeypatch):
    monkeypatch.setenv("RUNNER_HOLD_ENABLED", "false")
    assert "Core long-term holding rule" in RL.prompt_block(_stock(), market="US", language="en")
    assert RL.prompt_block(_stock(block=None), market="US", language="en") == ""


class _Agent:
    _no_order_effects = None


def test_guard_blocks_discretionary_sells_and_keeps_the_hard_stop(live, monkeypatch):
    monkeypatch.setenv("RUNNER_HOLD_ENABLED", "false")                # independent of the runner switch
    monkeypatch.setattr(RL, "_emit", lambda *a, **k: None)
    agent = _Agent()
    sell, reason = RL.guard_result(agent, "US", _stock(), True, "Tier 3 trend weakness: three lower closes")
    assert sell is False
    assert RL.guard_result(agent, "US", _stock(price=99.0), True, "stop-loss reached")[0] is True
    assert RL.guard_result(agent, "US", _stock(), True, "[CORP_EVENT] tender offer confirmed")[0] is True
    decision = RL.guard_decision(agent, "US", _stock(), {"should_sell": True, "sell_reason": "trailing stop broken",
                                                         "portfolio_adjustment": {"needed": True, "new_stop_loss": 140}})
    assert decision["should_sell"] is False and decision["portfolio_adjustment"]["new_stop_loss"] is None
    # off -> untouched
    monkeypatch.setenv("CORE_HOLD_LIVE_ENABLED", "false")
    assert RL.guard_result(agent, "US", _stock(), True, "Tier 3")[0] is True


def test_trend_exit_view_and_batch_review_use_the_core_view(live):
    view = RL.view_for_row("KR", _stock(), 150.0, lambda t: [])
    assert view["kind"] == "core" and view["phase"] and view["exit"] is None
    import asyncio
    stock = _stock()
    assert asyncio.run(RL.review_holding(_Agent(), "KR", stock)) is None and stock[RL.CTX_KEY]["kind"] == "core"


def test_sell_facts_drop_trailing_lines_for_a_core_holding(live):
    from prism_core import sell_facts as F
    bars = _bars([100.0 + i for i in range(30)])
    stock = dict(_stock(), buy_date="2026-01-01 10:00:00")
    text = F.prompt_block(market="US", language="en", stock_data=stock, highest_price=160.0, threshold_pct=3.0,
                          regime="strong_bull", bars=bars)
    assert "Trailing" not in text and "Stop raise" not in text


def _db(tmp_path, rows):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, buy_date TEXT, "
                 "buy_price REAL, scenario TEXT, stop_loss REAL)")
    conn.executemany("INSERT INTO us_stock_holdings VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def _trend(n=300, step=0.004):
    closes, price = [], 50.0
    for _ in range(n):
        closes.append(price)
        price *= 1 + step
    days, day = [], __import__("datetime").date(2025, 1, 1)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += __import__("datetime").timedelta(days=1)
    return [{"date": d, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c} for d, c in zip(days, closes)]


def _row(row_id, ticker, bars, entry_index, first, scenario="{}", stop=None):
    buy = f"{bars[entry_index]['date']} 23:30:00"            # KST evening = that New York session
    return (row_id, ticker, buy, first, scenario, stop if stop is not None else first * 0.93)


def _rows(conn):
    return {r[0]: (json.loads(r[1]).get(C.SCENARIO_KEY), r[2])
            for r in conn.execute("SELECT id, scenario, stop_loss FROM us_stock_holdings")}


def test_proof_marks_proven_and_moves_the_stop_to_the_first_buy(live, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_emit", lambda *a, **k: None)
    bars = _trend(300)
    first = bars[275]["close"] / 1.16                        # +16% on session 25 of an entry at bar 275
    conn = _db(tmp_path, [_row(1, "AAA", bars, 275, first, stop=bars[-1]["close"] * 0.95),     # raised trailing stop
                          _row(2, "BBB", bars, 290, bars[290]["close"])])                       # 10 sessions: no proof
    keyed = {f"AAA|{bars[275]['date']} 23:30": bars, f"BBB|{bars[290]['date']} 23:30": bars}
    sent = []
    actions = L.apply_live(conn, "US", keyed, alert=sent.append)
    assert [(a["action"], a["ticker"]) for a in actions] == [("proven", "AAA")]
    block, stop = _rows(conn)[1]
    assert block["status"] == L.PROVEN and block["proven_on"] == bars[-1]["date"] and stop == pytest.approx(first)
    assert _rows(conn)[2][0] is None and sent and "검증 통과" in sent[0]
    stock = {"scenario": json.dumps({C.SCENARIO_KEY: block}), "ticker": "AAA"}
    assert L.view(stock, "US")["status"] == L.PROVEN and L.exit_kind(stock, "US") == "proven_exit"
    assert "단계적 신뢰 규칙" in L.prompt_block(dict(stock, current_price=100.0), market="US", language="ko")
    assert "Staged-trust rule" in L.prompt_block(dict(stock, current_price=100.0), market="US", language="en")


def test_two_closes_below_ma50_after_proof_leave_a_pending_exit_that_sells(live, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(RL, "_emit", lambda *a, **k: None)
    bars = _trend(300)
    closes = C.closes_of(bars)
    for j in (298, 299):
        bars[j]["close"] = C.ma(closes, j, 50) * 0.97
    first = bars[250]["close"] / 1.2
    block = {"version": L.LIVE_VERSION, "status": L.PROVEN, "proven_on": bars[280]["date"], "entry_first": first}
    conn = _db(tmp_path, [_row(1, "AAA", bars, 250, first, json.dumps({C.SCENARIO_KEY: block}), first)])
    actions = L.apply_live(conn, "US", {f"AAA|{bars[250]['date']} 23:30": bars})
    assert [a["action"] for a in actions] == ["exit_pending"]
    stored, _ = _rows(conn)[1]
    stock = {"ticker": "AAA", "current_price": 90.0, "stop_loss": first, "buy_price": first,
             "scenario": json.dumps({C.SCENARIO_KEY: stored})}
    view = RL.view_for_row("US", stock, 90.0, lambda t: [])
    assert view["exit"] == L.PENDING_MA50
    assert RL.guard_result(_Agent(), "US", stock, True, L.exit_reason(view, "US", "en"))[0] is True
    import asyncio
    assert asyncio.run(RL.review_holding(_Agent(), "US", dict(stock))).startswith(L.PENDING_MA50)
    # one close below MA50 (or both before the proof day) is not an exit
    assert not L.two_closes_below_ma50(bars, after=bars[299]["date"])


def test_promotion_within_the_cap_and_skips_kept_proven(live, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_emit", lambda *a, **k: None)
    bars = _trend(300)
    first = bars[240]["close"] / 1.35                         # session 60, +35%
    proven = json.dumps({C.SCENARIO_KEY: {"version": L.LIVE_VERSION, "status": L.PROVEN,
                                          "proven_on": bars[270]["date"], "entry_first": first}})
    core = json.dumps({C.SCENARIO_KEY: {"version": L.LIVE_VERSION, "status": L.CORE, "entry_first": 10.0,
                                        "promoted_on": "2026-01-01"}})
    conn = _db(tmp_path, [_row(1, "OLD", bars, 100, 10.0, core, 10.0),
                          _row(2, "AAA", bars, 240, first, proven, first),
                          _row(3, "AAA", bars, 240, first, proven, first),          # a second account: one slot
                          _row(4, "BBB", bars, 241, first * 1.004, proven, first)])
    keyed = {f"{t}|{bars[i]['date']} 23:30": bars for t, i in (("OLD", 100), ("AAA", 240), ("BBB", 241))}
    actions = L.apply_live(conn, "US", keyed, regimes=lambda day: {"regime": "sideways", "date": day})
    kinds = sorted((a["action"], a["ticker"]) for a in actions if a["action"] != "stop_set")
    assert kinds == [("cap_skipped", "BBB"), ("promoted", "AAA"), ("promoted", "AAA")]
    rows = _rows(conn)
    assert rows[2][0]["status"] == L.CORE and rows[2][0]["regime"]["regime"] == "sideways"
    assert rows[4][0]["status"] == L.PROVEN and rows[4][0]["promotion_skipped"] == bars[-1]["date"]
    again = L.apply_live(conn, "US", keyed, regimes=lambda day: None)
    assert not [a for a in again if a["action"] in ("promoted", "cap_skipped")]                 # no replacement


def test_apply_live_does_nothing_while_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CORE_HOLD_LIVE_ENABLED", raising=False)
    bars = _trend(300)
    conn = _db(tmp_path, [_row(1, "AAA", bars, 275, bars[275]["close"] / 1.16, stop=1.0)])
    assert L.apply_live(conn, "US", {f"AAA|{bars[275]['date']} 23:30": bars}) == []
    assert _rows(conn)[1] == (None, 1.0)


def test_every_sale_path_records_core_exit():
    kr = (ROOT / "stock_tracking_agent.py").read_text(encoding="utf-8")
    us = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text(encoding="utf-8")
    assert kr.count('core_hold_live.exit_kind(stock_data, "KR") or exit_kind') == 2       # sell_stock + pending exit
    assert L.EXIT_KIND == {"PROVEN": "proven_exit", "CORE": "core_exit"}
    assert us.count('core_hold_live.exit_kind(stock_data, "US") or exit_kind') == 1
    seller = (ROOT / "tools/trend_exit_seller.py").read_text(encoding="utf-8")
    assert "runner_hold_live.enabled(market) or core_hold_live.enabled(market)" in seller
