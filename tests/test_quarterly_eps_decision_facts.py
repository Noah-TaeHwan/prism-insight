"""Quarterly EPS as numbers on the BUY decision trace (quarterly_eps_v1, 2026-10-23). Observation only.

The KR report step already collects the WiseReport quarterly table (#851) and the US step the yfinance
quarterly income statement; the decision ledger only had the prose. The same frames now leave a numeric
block in report_meta, and decision_inputs.shadow_captured copies it without a second fetch.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from observability import decision_inputs as D
from prism_core import kr_financial_summary as fs
from prism_core.decision_snapshot import quarterly_eps
from prism_core.us_quarterly_facts import quarterly_eps_summary as us_summary, quarterly_rows

ROOT = Path(__file__).resolve().parents[1]
QUARTERLY = (ROOT / "tests" / "fixtures" / "wisereport" / "cF1001_Q_018880.html").read_text(encoding="utf-8")
COMMON = dict(source="t", asof="2026-10-23T00:00:00+00:00", currency="KRW", eps_basis="reported_eps")


def _block(latest_eps, prior_eps, prior=True):
    return quarterly_eps({"period": "2026/06", "eps": latest_eps},
                         {"period": "2025/06", "eps": prior_eps} if prior else None, **COMMON)


def test_positive_base_gives_growth_rate():
    out = _block(150, 100)
    assert out["status"] == "OK" and out["missing"] == {}
    assert out["yoy_pct"] == 50.0 and out["turnaround"] is False and out["change"] == "yoy"
    assert out["contract"] == "quarterly_eps_v1" and out["prior_year_period"] == "2025/06"


def test_loss_base_is_turnaround_without_growth_rate():
    out = _block(84, -21)
    assert out["yoy_pct"] is None and out["missing"] == {"yoy_pct": "base_not_positive"}
    assert out["turnaround"] is True and out["change"] == "turned_to_profit" and out["status"] == "OK"


@pytest.mark.parametrize("now,prior,yoy,turn,change", [
    (-5, 50, -110.0, False, "turned_to_loss"),
    (0, 50, -100.0, False, "fell_to_zero"),
    (-9, -5, None, False, "loss_continued"),
    (0, 0, None, False, "zero_base"),
    (10, 0, None, True, "turned_to_profit"),
])
def test_sign_changes(now, prior, yoy, turn, change):
    out = _block(now, prior)
    assert (out["yoy_pct"], out["turnaround"], out["change"]) == (yoy, turn, change)


def test_missing_values_stay_null_never_zero():
    no_prior = _block(84, None, prior=False)
    assert no_prior["status"] == "PARTIAL" and no_prior["prior_year_eps"] is None
    assert no_prior["yoy_pct"] is None and no_prior["turnaround"] is None
    assert no_prior["missing"]["prior_year_quarter"] == "not_in_table"
    nan_eps = _block(math.nan, 100)
    assert nan_eps["eps"] is None and nan_eps["missing"]["eps"] == "not_reported"
    assert nan_eps["missing"]["yoy_pct"] == "inputs_missing" and nan_eps["status"] == "PARTIAL"
    none = quarterly_eps(None, None, reason="not_collected", **COMMON)
    assert none["status"] == "MISSING" and none["period"] is None and none["eps"] is None
    assert none["missing"] == {"latest_quarter": "not_collected"}


def test_kr_frame_uses_latest_reported_quarter_not_estimates():
    frame = fs.parse_financial_summary(QUARTERLY)
    out = fs.quarterly_eps_summary(frame, asof="2026-10-23T00:00:00+00:00")
    assert (out["period"], out["eps"], out["prior_year_period"], out["prior_year_eps"]) == ("2026/06", 84.0, "2025/06", -21.0)
    assert out["turnaround"] is True and out["yoy_pct"] is None and out["status"] == "OK"
    assert out["statement_basis"] == frame.attrs["basis"] and out["currency"] == "KRW"
    assert out["source"] == "wisereport_financial_summary_quarterly"
    # Same anchor as the report table (the two must never disagree).
    assert "EPS -21원 → 84원 (흑자전환)" in fs.render_quarterly_facts(frame, "ko")

    partial = fs.quarterly_eps_summary(frame.drop(index="2025/06"))
    assert partial["status"] == "PARTIAL" and partial["missing"]["prior_year_quarter"] == "not_in_table"
    assert fs.quarterly_eps_summary(None)["missing"] == {"latest_quarter": "not_collected"}
    assert fs.quarterly_eps_summary(None, reason="error_X")["missing"] == {"latest_quarter": "error_X"}
    estimates_only = fs.quarterly_eps_summary(frame[frame["estimate"]])
    assert estimates_only["status"] == "MISSING" and estimates_only["eps"] is None


def _statement(rows, quarters):
    columns = [pd.Timestamp(q) for q in quarters]
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(columns)}, index=list(rows))


def test_us_rows_use_diluted_eps_and_prior_year_quarter():
    stmt = _statement({"Total Revenue": [3.4e9, 3.2e9, 3.1e9, 3.3e9, 2.8e9],
                       "Diluted EPS": [0.6, 0.68, float("nan"), 0.89, 0.4]},
                      ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
    out = us_summary(quarterly_rows(stmt), asof="2026-10-23T00:00:00+00:00")
    assert (out["period"], out["eps"], out["prior_year_period"], out["prior_year_eps"]) == ("2026-06-30", 0.6, "2025-06-30", 0.4)
    assert out["yoy_pct"] == 50.0 and out["currency"] == "USD" and out["eps_basis"] == "diluted_eps"
    short = us_summary(quarterly_rows(stmt.iloc[:, :4]))
    assert short["status"] == "PARTIAL" and short["prior_year_eps"] is None
    assert us_summary(None)["missing"] == {"latest_quarter": "not_collected"}
    assert us_summary([])["missing"] == {"latest_quarter": "no_reported_quarter"}


class _Agent:
    pass


def _frame(n=40):
    days, day = [], date(2026, 7, 1)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    rows = [{"Open": 100 + i, "High": 101 + i, "Low": 99 + i, "Close": 100 + i, "Volume": 1000} for i in range(n)]
    return pd.DataFrame(rows, index=pd.to_datetime(days))


NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def _emit(agent, monkeypatch):
    sent = []
    monkeypatch.setattr(D, "emit_event", lambda name, **kw: sent.append(kw) or {"ok": 1})
    payload = D.emit_decision_inputs(agent, market="KR", ticker="018880", decision_id="d-q", scenario={},
                                     current_price=140.0, decision="entry", source="t", now=NOW)
    return payload, sent


def test_decision_inputs_carry_the_report_block_without_changing_the_decision(monkeypatch):
    block = fs.quarterly_eps_summary(fs.parse_financial_summary(QUARTERLY), asof="2026-10-23T00:30:00+00:00")
    agent = _Agent()
    D.capture_frame(agent, "018880", _frame(), market="KR")
    agent._report_meta = {"018880": {"peer_valuation": {"status": "MISSING"}}}
    prompt_before = D.prompt_facts(agent, "018880", market="KR", now=NOW)
    agent._report_meta["018880"]["quarterly_eps"] = block
    assert D.prompt_facts(agent, "018880", market="KR", now=NOW) == prompt_before   # prompt unaffected

    payload, sent = _emit(agent, monkeypatch)
    assert payload["quarterly_eps"] == block and payload["decision"] == "entry"
    assert payload["trading_impact"] == "none"
    from observability.events import build_event
    event = build_event("decision_inputs.shadow_captured", service="s", attributes=sent[0]["attributes"])
    assert "[REDACTED]" not in json.dumps(event["attributes"])
    assert event["attributes"]["quarterly_eps"]["eps"] == 84.0


def test_absent_or_late_block_is_missing(monkeypatch):
    agent = _Agent()
    payload, _ = _emit(agent, monkeypatch)
    assert payload["quarterly_eps"]["missing"] == {"latest_quarter": "not_in_report_meta"}
    late = quarterly_eps({"period": "2026/06", "eps": 1}, None, **dict(COMMON, asof="2026-10-23T02:00:00+00:00"))
    agent._report_meta = {"018880": {"quarterly_eps": late}}
    payload, _ = _emit(agent, monkeypatch)
    assert payload["quarterly_eps"] == {"contract": "quarterly_eps_v1", "status": "MISSING",
                                        "missing": {"latest_quarter": "collected_after_decision"}}


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_report_steps_hand_the_block_to_the_tracking_agents():
    assert "report_meta['quarterly_eps'] = quarterly_eps_summary(quarterly_frame" in _source("cores/analysis.py")
    us = _source("prism-us/cores/us_analysis.py")
    assert "report_meta['quarterly_eps'] = quarterly_eps_summary(prefetched.get('quarterly_results'))" in us
    orchestrator = _source("prism-us/us_stock_analysis_orchestrator.py")
    assert "report_meta=report_meta," in orchestrator
    assert 'report_meta=getattr(self, "report_meta", None),' in orchestrator
    assert "self._report_meta = report_meta if isinstance(report_meta, dict) else {}" in _source(
        "prism-us/us_stock_tracking_agent.py")
