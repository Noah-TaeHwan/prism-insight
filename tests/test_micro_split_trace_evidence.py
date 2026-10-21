"""Micro-split and runner events land on the position's trade trace, and the first size keeps its
inputs (user request 2026-10-21). Observation only."""
import hashlib
import json

from observability import holding_decisions
from prism_core import micro_split_live as live
from prism_core import runner_hold_live
from prism_core.trace_ids import decision_of, trade_trace


def test_trade_trace_matches_the_buy_and_sell_events():
    expected = hashlib.sha256(b"trade-trace|KR|d-1").hexdigest()[:32]
    assert trade_trace("kr", decision_id="d-1", position_id="legacy:KR:9") == expected
    assert trade_trace("KR", position_id="legacy:KR:9") == hashlib.sha256(b"trade-trace|KR|legacy:KR:9").hexdigest()[:32]
    assert trade_trace("KR") is None
    assert decision_of(json.dumps({"_decision_id": " d-1 "})) == "d-1" and decision_of("not json") is None


def test_holding_reviews_and_runner_events_share_one_trace(monkeypatch):
    sent = []
    import observability.events as events
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    monkeypatch.setattr(holding_decisions, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1},
                        raising=False)
    stock = {"id": 7, "ticker": "000001", "scenario": json.dumps({"_decision_id": "d-7"}), "buy_price": 100,
             "current_price": 110}
    runner_hold_live._emit("runner.detected", market="KR", ticker="000001", row_id=7, stock=stock, attributes={})
    (name, kw), = [s for s in sent if s[0] == "runner.detected"]
    assert kw["trace_id"] == trade_trace("KR", decision_id="d-7") and kw["decision_id"] == "d-7"


def test_add_plan_event_at_buy_time_uses_the_decision_trace(monkeypatch):
    sent = []
    import observability.events as events
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    block = {"allocation": "0.35"}
    live._emit_planned(market="US", ticker="AAA", position_id=None, block=block, plan=None, issues=[],
                       decision_id="d-9")
    (name, kw), = sent
    assert name == "micro_split.add_planned" and kw["trace_id"] == trade_trace("US", decision_id="d-9")


def test_entry_record_keeps_the_sizing_inputs():
    plan = {"policy_version": "v3-ae", "plan_hash": "h", "initial_nominal": "0.3500", "entry_reference": "100",
            "initial_stop": "94", "risk_limit": "0.06", "stop_proxy": "0.05", "setup": {"atr14": "3.33"}}
    block = live.entry_record(plan=plan, unit_amount=1000, market="US", entered_at="2026-10-21T00:00:00Z")
    assert block["sizing"] == {"atr14": "3.33", "stop_proxy": "0.05", "initial_stop": "94", "risk_limit": "0.06",
                               "initial_nominal": "0.3500"}
    assert block["allocation"] == "0.3500"                     # sizing itself is unchanged
