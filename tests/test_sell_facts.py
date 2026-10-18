"""System-computed sell facts (prism_core/sell_facts.py) match the exit code and never guess (2026-10-19 audit)."""
import json
from pathlib import Path

import pytest

from cores.oneil_fallback import SellInputs, evaluate_oneil_sell
from prism_core import sell_facts as F

ROOT = Path(__file__).resolve().parents[1]


def _bars(closes, volumes=None, start_day=1):
    volumes = volumes or [100.0] * len(closes)
    return [{"date": f"2026-09-{start_day + i:02d}", "close": c, "volume": v}
            for i, (c, v) in enumerate(zip(closes, volumes))]


def test_trailing_line_and_trigger_are_the_exit_code_values():
    for regime, drop in (("strong_bull", 0.92), ("sideways", 0.95), (None, 0.90)):
        t = F.trailing_state(buy_price=100, highest_price=120, stop_loss=93, current_price=115, regime=regime,
                             threshold_pct=3.0, last_close=115)
        assert t["activated"] and t["drop"] == drop
        assert t["line"] == pytest.approx(120 * drop) and t["trigger"] == pytest.approx(120 * drop * 0.995)
        # the exit code sells exactly at or below the stated trigger
        live = bool(regime)
        just_below = SellInputs(100, t["trigger"] - 0.01, 0, 0, 120, regime or "", regime_is_live=live)
        just_above = SellInputs(100, t["trigger"] + 0.01, 0, 0, 120, regime or "", regime_is_live=live)
        assert evaluate_oneil_sell(just_below)[0] and not evaluate_oneil_sell(just_above)[0]


def test_stop_raise_needs_activation_threshold_live_regime_and_a_line_below_the_price():
    base = dict(buy_price=100, highest_price=120, current_price=115, regime="strong_bull", last_close=115)
    assert F.trailing_state(stop_loss=93, threshold_pct=3.0, **base)["raise_ok"]          # 110.4 vs 93: +18.7%
    assert not F.trailing_state(stop_loss=109, threshold_pct=3.0, **base)["raise_ok"]     # +1.3% < threshold
    assert not F.trailing_state(stop_loss=93, threshold_pct=3.0, **{**base, "regime": None})["raise_ok"]
    assert not F.trailing_state(stop_loss=93, threshold_pct=3.0, **{**base, "current_price": 110})["raise_ok"]
    off = F.trailing_state(stop_loss=93, threshold_pct=3.0, **{**base, "highest_price": 104})
    assert not off["activated"] and not off["raise_ok"]


def test_tier3_conditions_on_completed_bars():
    closes = [100.0] * 17 + [104.0, 103.0, 102.0, 95.0]           # 3 lower closes, last close under the 20-day MA
    volumes = [100.0] * 20 + [150.0]
    t = F.trend_weakness(_bars(closes, volumes))
    assert t["three_down"] and t["volume_above"] and t["volume_ratio"] == pytest.approx(1.5)
    assert t["below_ma20"] and t["met"]
    t = F.trend_weakness(_bars(closes, [100.0] * 20 + [80.0]))
    assert t["volume_above"] is False and t["met"] is False
    # not enough history -> unknown, never filled
    short = F.trend_weakness(_bars([100.0, 99.0, 98.0, 97.0]))
    assert short["three_down"] is True and short["ma20"] is None and short["volume_ratio"] is None
    assert short["met"] is None
    assert F.trend_weakness([]) is None


def test_sessions_held_counts_completed_sessions_after_the_entry_session():
    bars = _bars([100.0] * 6, start_day=14)                          # 09-14 .. 09-19
    assert F.sessions_held(bars, "KR", "2026-09-15 10:00:00") == 4
    assert F.sessions_held(bars, "KR", "not a date") is None


def test_average_entry_only_for_multi_leg_micro_split():
    legs = [{"price": "10000", "allocation": "0.5"}, {"price": "11000", "allocation": "0.5"}]
    scenario = json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": "1", "legs": legs}})
    avg, n = F.average_entry(scenario)
    assert n == 2 and avg == pytest.approx(1 / (0.5 / 10000 + 0.5 / 11000))
    one = json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": "0.5", "legs": legs[:1]}})
    assert F.average_entry(one) is None and F.average_entry("{}") is None


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_block_states_the_facts(language):
    closes = [100.0] * 17 + [104.0, 103.0, 102.0, 95.0]
    stock = {"ticker": "005930", "buy_price": 90.0, "current_price": 96.0, "stop_loss": 85.0,
             "buy_date": "2026-09-02 10:00:00", "scenario": "{}"}
    text = F.prompt_block(market="KR", language=language, stock_data=stock, highest_price=110.0, threshold_pct=3.0,
                          regime="sideways", bars=_bars(closes, [100.0] * 20 + [150.0]))
    if language == "ko":
        assert "시스템 계산 사실" in text and "× 0.95" in text and "104원" in text and "매수 후 19거래일째" in text
        # peak 110 x 0.95 = 104.5 is above the price 96: no raise, and the confirmed close 95 is below -> sell
        assert "손절선 상향 자격: 없음" in text and "라인 아래 → 매도" in text and "3개 모두 충족 예" in text
    else:
        assert "System-computed facts" in text and "× 0.95" in text and "Stop raise qualifies: no" in text
        assert "below it → sell" in text
        assert "all three met yes" in text and "19 sessions since the entry" in text


def test_prompt_block_says_unknown_when_bars_fail_and_skips_trailing_for_runners(monkeypatch):
    monkeypatch.setenv("RUNNER_HOLD_ENABLED", "true")
    stock = {"ticker": "AAPL", "buy_price": 100.0, "current_price": 130.0, "stop_loss": 100.0,
             "buy_date": "2026-09-02 10:00:00",
             "scenario": "{}"}
    from prism_core import runner_hold as R
    stock["scenario"] = json.dumps({R.SCENARIO_KEY: {"version": R.VERSION, "status": R.RUNNER}})
    text = F.prompt_block(market="US", language="ko", stock_data=stock, highest_price=130.0, threshold_pct=3.0,
                          regime="strong_bull", bars=[])
    assert "확정 일봉: 확인 불가" in text
    assert R.record(stock["scenario"]) and "트레일링" not in text   # the runner rule supersedes trailing


def test_prompt_block_safe_never_raises(monkeypatch):
    from prism_core import runner_hold_live as L
    monkeypatch.setenv("SELL_FACTS_ENABLED", "true")
    monkeypatch.setattr(L, "fetch_daily_bars", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    text = F.prompt_block_safe(market="KR", language="ko", stock_data={"ticker": "005930", "buy_price": 100,
                               "current_price": 101, "stop_loss": 93, "buy_date": "2026-10-01 10:00:00",
                               "scenario": "{}"}, highest_price=101, threshold_pct=3.0, regime="sideways")
    assert "확인 불가" in text


@pytest.mark.parametrize("path", ["stock_tracking_enhanced_agent.py", "prism-us/us_stock_tracking_agent.py"])
def test_both_sell_reviews_append_the_block(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "sell_facts_block, market=" in src


def test_switch_off_returns_nothing(monkeypatch):
    monkeypatch.setenv("SELL_FACTS_ENABLED", "false")
    assert F.prompt_block_safe(market="KR", language="ko", stock_data={"ticker": "005930"}, highest_price=1,
                               threshold_pct=3.0, regime=None) == ""
