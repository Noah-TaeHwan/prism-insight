"""Core long-term holding LIVE wiring behind CORE_HOLD_LIVE_ENABLED (default off), H-1023-corehold."""
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


def _result(ticker, buy, date, *, share=0.5, ret=0.3, exit_=None):
    return {"key": f"{ticker}|{buy[:16]}", "ticker": ticker, "open": True, "entry_avg": 100.0,
            "a": {"date": date, "share": share, "ret": ret, "exit": exit_, "view": {}, "regime": {"regime": "sideways"}}}


def test_apply_live_promotes_within_the_cap_and_sets_stops(live, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_emit", lambda *a, **k: None)
    held = json.dumps({C.SCENARIO_KEY: BLOCK})
    conn = _db(tmp_path, [(1, "OLD", "2026-01-01 10:00:00", 50.0, held, 50.0),
                          (2, "AAA", "2026-03-01 10:00:00", 100.0, "{}", 120.0),
                          (3, "AAA", "2026-03-01 10:00:00", 100.0, "{}", 120.0),     # second account
                          (4, "BBB", "2026-03-02 10:00:00", 100.0, "{}", 95.0)])
    results = [_result("AAA", "2026-03-01 10:00:00", "2026-05-01", share=0.9),
               _result("BBB", "2026-03-02 10:00:00", "2026-05-01", share=0.1)]
    bars = [{"date": f"d{i:03d}", "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0} for i in range(210)]
    keyed = {r["key"]: bars for r in results} | {"OLD|2026-01-01 10:00": bars}
    sent = []
    actions = L.apply_live(conn, "US", results, keyed, alert=sent.append)
    kinds = sorted((a["action"], a["ticker"]) for a in actions)
    assert ("cap_full", "BBB") in kinds and kinds.count(("promoted", "AAA")) == 2   # one slot left: AAA ranks first
    rows = {r[0]: r for r in conn.execute("SELECT id, scenario, stop_loss FROM us_stock_holdings")}
    block = json.loads(rows[2][1])[C.SCENARIO_KEY]
    assert block["status"] == L.CORE and block["regime"]["regime"] == "sideways" and rows[2][2] == 100.0
    assert json.loads(rows[4][1])[C.SCENARIO_KEY]["status"] == "CAP_SKIPPED" and rows[4][2] == 95.0  # stop kept
    assert sent and "승격" in sent[0]
    assert L.apply_live(conn, "US", results, keyed) == []                               # idempotent


def test_apply_live_does_nothing_while_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CORE_HOLD_LIVE_ENABLED", raising=False)
    conn = _db(tmp_path, [(2, "AAA", "2026-03-01 10:00:00", 100.0, "{}", 120.0)])
    assert L.apply_live(conn, "US", [_result("AAA", "2026-03-01 10:00:00", "2026-05-01")], {}) == []
    assert conn.execute("SELECT stop_loss FROM us_stock_holdings").fetchone()[0] == 120.0


def test_every_sale_path_records_core_exit():
    kr = (ROOT / "stock_tracking_agent.py").read_text(encoding="utf-8")
    us = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text(encoding="utf-8")
    assert kr.count('core_hold_live.exit_kind(stock_data, "KR") or exit_kind') == 2       # sell_stock + pending exit
    assert us.count('core_hold_live.exit_kind(stock_data, "US") or exit_kind') == 1
    seller = (ROOT / "tools/trend_exit_seller.py").read_text(encoding="utf-8")
    assert "runner_hold_live.enabled(market) or core_hold_live.enabled(market)" in seller
