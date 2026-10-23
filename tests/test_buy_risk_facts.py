"""BUY risk facts (buy_risk_facts_v1, 2026-10-23): the gate's own stop/R-R numbers, Step 3 items the code
knows, recent stop-outs and the sector cap, as a per-stock BUY prompt block behind PRISM_BUY_RISK_FACTS
(default off) and on the decision ledger. Facts only: the gate decides exactly as before.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cores.buy_gate import REGIME_RULES, evaluate_production_buy_gate
from observability import decision_inputs as D
from prism_core import buy_risk_facts as R

ROOT = Path(__file__).resolve().parents[1]


def _facts_line(days):
    return f"- Market Pulse: UPTREND | 분산일(distribution days, 최근 25세션): {days} (x)"


def _gate(regime, days, *, stop, target, price=100.0):
    scenario = {"decision": "진입", "effective_score": 10, "min_score": 0, "stop_loss": stop, "target_price": target,
                "momentum_signal_count": 5, "additional_confirmation_count": 5}
    return evaluate_production_buy_gate(scenario, current_price=price, market_regime=regime,
                                        trend_facts=_facts_line(days))


def _codes(result):
    return {item["code"] for item in result["findings"]}


@pytest.mark.parametrize("regime, days, effective", [
    ("strong_bull", 2, "strong_bull"),
    ("sideways", 3, "sideways"),
    ("moderate_bull", 7, "sideways"),       # distribution step-down
    ("moderate_bear", 6, "strong_bear"),    # step-down at exactly the threshold
    ("strong_bear", 9, "strong_bear"),
])
def test_stop_band_and_target_equal_the_gate(regime, days, effective):
    facts = R.stop_rr_facts(regime, _facts_line(days), 100.0, tick=0.01)
    rule = REGIME_RULES[effective]
    assert facts["status"] == "OK" and facts["effective_regime"] == effective
    assert facts["max_loss_pct"] == rule["max_loss_pct"] and facts["rr_floor"] == rule["rr_floor"]
    assert facts["support_floor_loss_pct"] == rule["max_loss_pct"] / 2

    at_max = _gate(regime, days, stop=facts["max_stop_price"], target=facts["min_target_at_max_stop"])
    assert at_max["effective_regime"] == effective
    assert at_max["distribution_caution"] == facts["distribution_step_down"]
    assert not _codes(at_max) & {"rr_below_floor", "stop_exceeds_regime_limit"}
    assert rule["rr_floor"] <= at_max["recomputed_rr"] < rule["rr_floor"] + 0.01   # tick-rounded up
    at_floor = _gate(regime, days, stop=facts["floor_stop_price"], target=facts["min_target_at_floor_stop"])
    assert not _codes(at_floor) & {"rr_below_floor", "stop_exceeds_regime_limit"}

    # Just past either number, the same gate refuses: the block states its boundary, not a looser one.
    short = _gate(regime, days, stop=facts["max_stop_price"], target=facts["min_target_at_max_stop"] - 0.2)
    assert "rr_below_floor" in _codes(short)
    wide = _gate(regime, days, stop=facts["max_stop_price"] - 0.5, target=200.0)
    assert "stop_exceeds_regime_limit" in _codes(wide)


def test_missing_regime_or_price_is_missing_not_zero():
    none = R.stop_rr_facts(None, _facts_line(7), 100.0)
    assert none["status"] == "MISSING" and "max_stop_price" not in none
    no_price = R.stop_rr_facts("sideways", "", None)
    assert no_price["status"] == "PARTIAL" and no_price["distribution_days"] is None
    assert no_price["max_loss_pct"] == 6.0 and "max_stop_price" not in no_price
    text = R.render({"stop_rr": no_price}, market="KR", language="ko")
    assert "최대 손절폭 손절가 결측" in text and "강등 없음" in text


def test_step_16_constants_match_the_reentry_counterfactual():
    from prism_core.reentry_campaign import G1_SESSIONS, G1_STOPS
    assert (R.STOPOUT_WINDOW_DAYS, R.REPEAT_STOPS) == (G1_SESSIONS, G1_STOPS)


def test_primary_support_floor_matches_every_buy_prompt():
    for path in ("cores/agents/trading_agents.py", "prism-us/cores/agents/trading_agents.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "매트릭스 최대 손절폭의 50%를 floor로 채택" in text
        assert "Use 50% of the matrix max stop as a floor" in text
    assert R.SUPPORT_FLOOR_FRACTION == 0.5


def test_momentum_reuses_trend_fact_verdicts_and_keeps_missing():
    out = R.momentum_facts({"volume_signal1": "met", "near_high": False}, market="KR")
    assert out["signals"] == {"volume_200pct": "MET", "investor_flow_3d": "MISSING", "near_52w_high": "NOT_MET",
                              "sector_uptrend": "MISSING", "box_breakout": "MISSING"}
    assert out["met_count"] == 1 and out["missing_count"] == 3
    pending = R.momentum_facts({"volume_signal1": "undetermined"}, market="US")
    assert pending["signals"]["volume_200pct"] == "UNDETERMINED"
    assert pending["signals"]["investor_flow_3d"] == "NOT_EVALUATED"     # US prompt: not evaluated (scores 0)
    assert pending["signals"]["near_52w_high"] == "MISSING" and pending["met_count"] == 0
    assert R.momentum_facts(None, market="KR")["missing_count"] == 5


def _weekdays(n, end=date(2026, 10, 23)):
    days, day = [], end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def test_stopouts_count_trading_days_and_keep_unknown_kinds_unknown():
    days = _weekdays(30)
    d = lambda i: days[i].isoformat()  # noqa: E731
    rows = [(d(-4) + " 09:10:00", d(-2) + " 10:00:00", -6.0, "stop"),
            (d(-12), d(-8), -5.1, None),
            (d(-25), d(-11), 12.0, "ai")]               # sold before the window
    out = R.stopout_facts(rows, days)
    assert out["window_start"] == d(-10) and out["stop_count"] == 1 and out["unknown_kind_count"] == 1
    assert out["exits"][0]["trading_days_held"] == 2 and out["quick_stop_count"] == 1
    assert out["repeat_stop_condition"] is None                     # 1 stop + 1 unknown: undecided
    rows.append((d(-7), d(-6), -4.0, "stop"))
    assert R.stopout_facts(rows, days)["repeat_stop_condition"] is True
    assert R.stopout_facts([(d(-3), d(-1), 5.0, "target")], days)["repeat_stop_condition"] is False
    assert R.stopout_facts(rows, days[:5])["status"] == "MISSING"
    text = R.render({"stopouts": R.stopout_facts(rows, days)}, market="KR", language="ko")
    assert "손절(stop) 2회" in text and "1.6단계 횟수 조건(약 2주 내 손절 2회 이상): 예" in text
    missing = R.render({"stopouts": {"status": "MISSING"}}, market="US", language="en")
    assert "recent sells: missing (unknown, not 'none')" in missing


def test_sector_counts_and_unknown_sector():
    held = [json.dumps({"sector": "IT"}), json.dumps({"sector": "it"}), json.dumps({"sector": "Bank"}), None, "x"]
    out = R.sector_facts("IT", held, same_sector=lambda a, b: a.lower() == b.lower(), max_same=3, ratio=0.3,
                         min_for_ratio=4, allowed=True, used_slots=2.4, slot_cap=10)
    assert out["same_sector_count"] == 2 and out["sector_holdings"] == 3 and out["cap_allows_new_entry"] is True
    unknown = R.sector_facts("Unknown", held, same_sector=str.__eq__, max_same=3, ratio=0.3, min_for_ratio=4,
                             allowed=True, used_slots=None, slot_cap=10)
    assert unknown["status"] == "MISSING"
    assert "업종 한도: 결측(업종 미확인); 실제 사용 비중 결측" in R.render({"sector": unknown}, market="KR")


class _Agent:
    MAX_SAME_SECTOR, SECTOR_CONCENTRATION_RATIO, max_slots = 3, 0.3, 10

    def __init__(self, market="KR"):
        self.conn = sqlite3.connect(":memory:")
        self.cursor = self.conn.cursor()
        table = "trading_history" if market == "KR" else "us_trading_history"
        self.cursor.execute(f"CREATE TABLE {table} (account_key TEXT, ticker TEXT, buy_date TEXT, sell_date TEXT, "
                            "profit_rate REAL, exit_kind TEXT)")
        self.table = table

    def _account_scope(self):
        return ("acct-a", "A")


def _collect(agent, market="KR", **extra):
    return R.collect(agent, "005930", market=market, language="ko", regime="moderate_bull",
                     trend_facts=_facts_line(7), sector="IT", holding_scenarios=[json.dumps({"sector": "IT"})],
                     sector_allowed=True, same_sector=lambda a, b: a == b, ratio_min_holdings=4, **extra)


def test_flag_off_builds_ledger_copy_but_no_prompt(monkeypatch):
    monkeypatch.delenv("PRISM_BUY_RISK_FACTS", raising=False)
    agent = _Agent()
    days = _weekdays(30)
    R.remember_trend_inputs(agent, "005930", price=70000, volume={"signal1": "met"}, high52={"near_high": True},
                            dates=[datetime.combine(d, datetime.min.time()) for d in days])
    rows = [("acct-a", "005930", days[-5].isoformat(), days[-3].isoformat(), -6.5, "stop"),
            ("acct-b", "005930", days[-5].isoformat(), days[-2].isoformat(), -6.5, "stop"),   # other account
            ("acct-a", "000660", days[-5].isoformat(), days[-2].isoformat(), -6.5, "stop")]   # other ticker
    agent.cursor.executemany("INSERT INTO trading_history VALUES (?,?,?,?,?,?)", rows)
    assert _collect(agent) == ""
    facts = agent._buy_risk_facts["005930"]
    assert facts["prompt_included"] is False and facts["stopouts"]["stop_count"] == 1
    assert facts["stop_rr"]["max_stop_price"] == pytest.approx(70000 * 0.94)
    assert facts["momentum"]["met_count"] == 2 and facts["sector"]["same_sector_count"] == 1

    monkeypatch.setenv("PRISM_BUY_RISK_FACTS", "true")
    block = _collect(agent)
    assert block == "\n" + R.render(agent._buy_risk_facts["005930"], market="KR", language="ko")
    assert agent._buy_risk_facts["005930"]["prompt_included"] is True
    assert "65,800원" in block and "75,460원" in block      # 70,000 x (1-6%) and x (1+1.3x6%)


def test_unreadable_history_is_missing(monkeypatch):
    monkeypatch.setenv("PRISM_BUY_RISK_FACTS", "true")
    agent = _Agent(market="US")
    agent.cursor.execute("DROP TABLE us_trading_history")
    block = _collect(agent, market="US")
    assert agent._buy_risk_facts["005930"]["stopouts"]["status"] == "MISSING"
    assert "이 종목 최근 매도 기록: 결측" in block
    assert "3거래일 연속 순매수" not in block    # US item 2: "not evaluated, do not mention"


def test_decision_ledger_carries_the_same_block(monkeypatch):
    sent = []
    monkeypatch.setattr(D, "emit_event", lambda name, **kw: sent.append(kw) or {"ok": 1})
    agent = _Agent()
    _collect(agent)
    now = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
    payload = D.emit_decision_inputs(agent, market="KR", ticker="005930", decision_id="d-r", scenario={},
                                     current_price=100.0, decision="no_entry", source="t", now=now)
    assert payload["buy_risk_facts"] == agent._buy_risk_facts["005930"]
    assert payload["trading_impact"] == "none"
    from observability.events import build_event
    event = build_event("decision_inputs.shadow_captured", service="s", attributes=sent[0]["attributes"])
    assert "[REDACTED]" not in json.dumps(event["attributes"]["buy_risk_facts"], ensure_ascii=False)
    other = D.emit_decision_inputs(agent, market="KR", ticker="000660", decision_id="d-s", scenario={},
                                   current_price=100.0, decision="no_entry", source="t", now=now)
    assert other["buy_risk_facts"]["status"] == "NOT_COLLECTED"


@pytest.mark.parametrize("market, price", [("KR", 312000.0), ("KR", 1234.0), ("US", 57.31), ("US", 9.99),
                                           ("US", 1187.4)])
def test_tick_rounded_levels_pass_the_gate_for_every_regime(market, price):
    for regime in REGIME_RULES:
        for days in (0, 6):
            facts = R.stop_rr_facts(regime, _facts_line(days), price, tick=R.TICK[market])
            for stop, target in ((facts["max_stop_price"], facts["min_target_at_max_stop"]),
                                 (facts["floor_stop_price"], facts["min_target_at_floor_stop"])):
                result = _gate(regime, days, stop=stop, target=target, price=price)
                assert not _codes(result) & {"rr_below_floor", "stop_exceeds_regime_limit"}, (regime, days, stop)
