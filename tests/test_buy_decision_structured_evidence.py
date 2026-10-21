"""Buy decisions keep structured evidence for later analysis (user request 2026-10-21): stable skip
codes with the gate numbers, the decision snapshot on the decision's own trace. Observation only."""
import ast
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from observability import decision_inputs as DI
from prism_core import skip_reasons as SR

ROOT = Path(__file__).resolve().parents[1]


def test_skip_codes_follow_the_reason_order_and_never_come_back_empty():
    gate = {"allowed": False, "effective_regime": "sideways", "recomputed_rr": 0.9,
            "score_policy": {"required_score": 5},
            "findings": [{"code": "rr_below_floor", "hard": True}, {"code": "distribution_day_caution", "hard": False}]}
    out = SR.decision_fields(ai_entry=True, score_below_min=True, sector_blocked=True, gate=gate)
    assert out["skip_reason_codes"] == ["SCORE_BELOW_MIN", "SECTOR_CAP", "DETERMINISTIC_GATE"]
    assert out["gate_hard_codes"] == ["rr_below_floor"] and out["gate_soft_codes"] == ["distribution_day_caution"]
    assert out["rr_floor"] == 1.3 and out["max_loss_pct"] == 6.0 and out["recomputed_rr"] == 0.9
    assert out["required_score"] == 5
    assert SR.skip_codes(ai_entry=False, gate=gate) == ["AI_NO_ENTRY"]           # the gate never ran for it
    assert SR.skip_codes(ai_entry=True, gate={"allowed": True}) == ["OTHER"]
    assert SR.skip_codes(ai_entry=True, cooldown=True, regime_floor=True) == ["REENTRY_COOLDOWN", "REGIME_SCORE_FLOOR"]
    assert SR.gate_numbers(None) == {}


def _frame(n=270, end="2026-10-20"):
    days = pd.bdate_range(end=end, periods=n)
    closes = [100 + k * 0.3 for k in range(n)]
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
                         "Close": closes, "Volume": [1000.0 + (k % 3) * 10 for k in range(n)]}, index=days)


class _Agent:
    pass


def test_decision_inputs_carry_the_snapshot_on_the_decision_trace(monkeypatch):
    sent = []
    monkeypatch.setattr(DI, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    agent = _Agent()
    frame = _frame()                                           # includes today's (10/20) forming bar
    DI.capture_frame(agent, "AAA", frame, market="US", index_frame=_frame())
    now = datetime(2026, 10, 20, 17, 50, tzinfo=timezone.utc)   # 13:50 ET, session open
    DI.emit_decision_inputs(agent, market="US", ticker="AAA", decision_id="d-1", scenario={}, current_price=180.0,
                            decision="entry", source="test", now=now, earnings_lookup=lambda t, d: {})
    (name, kw), = sent
    assert kw["trace_id"] == hashlib.sha256(b"trade-trace|US|d-1").hexdigest()[:32]
    snap = kw["attributes"]["snapshot"]
    assert snap["contract"] == "decision_snapshot_v1" and snap["price"] == 180.0
    assert snap["last_completed_date"] == "2026-10-19"         # the forming bar is not a completed bar
    assert snap["ma200"] is not None and snap["rs_60d_pctp"] is not None
    assert len(agent._decision_input_bars["AAA"]["bars"]) == 80   # the prompt-facts input is unchanged


def test_missing_history_stays_unknown(monkeypatch):
    sent = []
    monkeypatch.setattr(DI, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    DI.emit_decision_inputs(_Agent(), market="KR", ticker="000001", decision_id="d-2", scenario={},
                            current_price=1.0, decision="Skip", source="t")
    assert sent[0][1]["attributes"]["snapshot"]["status"] == "MISSING"


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_every_skip_site_passes_the_codes_to_the_watchlist_event():
    assert "skip_detail=_skip_detail" in _source("stock_tracking_enhanced_agent.py")
    for path in ("stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"):
        src = _source(path)
        assert 'skip_detail=state.get("skip_detail")' in src and "decision_context.update(skip_detail)" in src
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_save_watchlist_item")
        assert "skip_detail" in [a.arg for a in fn.args.args + fn.args.kwonlyargs]
