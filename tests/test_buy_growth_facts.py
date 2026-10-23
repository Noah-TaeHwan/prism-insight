"""KR BUY growth facts (buy_growth_facts_v1, 2026-10-23): C/A growth rates and the debt ratio from the
report step's WiseReport frames. Fixtures only, no network. Observation in the ledger; prompt only by flag.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from observability import decision_inputs as D
from prism_core import buy_growth_facts as G
from prism_core import kr_financial_summary as fs

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "wisereport"
QUARTERLY = fs.parse_financial_summary((FIXTURES / "cF1001_Q_018880.html").read_text(encoding="utf-8"))
ANNUAL = fs.parse_financial_summary((FIXTURES / "cF1001_252990.html").read_text(encoding="utf-8"))
ASOF = "2026-10-23T00:30:00+00:00"
NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv("PRISM_BUY_GROWTH_FACTS", raising=False)


def test_parser_reads_debt_ratio_and_equity_rows():
    assert QUARTERLY.at["2026/06", "debt_ratio"] == 157.17 and QUARTERLY.at["2026/06", "total_equity"] == 42673.0
    assert ANNUAL.at["2025/12", "debt_ratio"] == 52.36


def test_quarterly_growth_exact_numbers_and_negative_base_labels():
    q = G.build(QUARTERLY, ANNUAL, asof=ASOF)["quarterly"]
    assert (q["period"], q["prior_year_period"], q["previous_period"]) == ("2026/06", "2025/06", "2026/03")
    m = q["metrics"]
    assert (m["revenue"]["prior_year_value"], m["revenue"]["value"], m["revenue"]["yoy_pct"]) == (28582.0, 28752.0, 0.5948)
    assert m["operating_income"]["yoy_pct"] == 61.2753 and m["operating_income"]["change"] == "yoy"
    assert m["operating_income"]["previous_quarter_value"] == 972.0       # F1: latest two quarters
    # Loss base: a label, never a growth rate (same rule as quarterly_eps_v1).
    assert m["eps"]["yoy_pct"] is None and m["eps"]["change"] == "turned_to_profit"
    assert m["net_income_controlling"]["change"] == "turned_to_profit"
    assert q["missing"]["eps.yoy_pct"] == "base_not_positive"
    # Five reported quarters: the previous quarter's prior-year quarter (2025/03) is absent -> MISSING, never False.
    assert q["previous_prior_year_period"] is None
    assert all(m[k]["accelerating"] is None for k in G.QUARTER_METRICS)
    assert q["missing"]["revenue.accelerating"] == "previous_quarter_prior_year_not_in_table"
    assert q["missing"]["eps.accelerating"] == "latest_yoy_unavailable" and q["status"] == "PARTIAL"
    # Same anchor and EPS as the ledger's quarterly_eps_v1.
    eps = fs.quarterly_eps_summary(QUARTERLY, asof=ASOF)
    assert (eps["period"], eps["eps"], eps["prior_year_eps"], eps["change"]) == \
        (q["period"], m["eps"]["value"], m["eps"]["prior_year_value"], m["eps"]["change"])


def test_acceleration_when_six_quarters_are_reported():
    frame = QUARTERLY.copy()
    frame.loc["2025/03"] = frame.loc["2025/06"]
    frame.loc["2025/03", ["revenue", "operating_income", "eps"]] = [27000.0, 800.0, 50.0]
    frame = frame.sort_index()
    m = G.quarterly_growth(frame)["metrics"]
    # 2026/03 vs 2025/03: revenue 27482/27000 -> +1.7852%, op income 972/800 -> +21.5%.
    assert m["revenue"]["previous_quarter_yoy_pct"] == 1.7852 and m["revenue"]["accelerating"] is False
    assert m["operating_income"]["previous_quarter_yoy_pct"] == 21.5 and m["operating_income"]["accelerating"] is True
    assert m["eps"]["accelerating"] is None          # latest EPS is a turnaround, no rate to compare


@pytest.mark.parametrize("prior,now,yoy,change", [
    (100, 150, 50.0, "yoy"), (50, -5, -110.0, "turned_to_loss"), (50, 0, -100.0, "fell_to_zero"),
    (-21, 84, None, "turned_to_profit"), (0, 10, None, "turned_to_profit"), (-9, -5, None, "loss_continued"),
    (0, 0, None, "zero_base"), (None, 5, None, None), (5, None, None, None),
])
def test_growth_sign_rules(prior, now, yoy, change):
    assert G.growth(prior, now) == (yoy, change)


def test_annual_eps_last_three_years():
    a = G.build(QUARTERLY, ANNUAL, asof=ASOF)["annual_eps"]
    assert [(y["period"], y["prior_eps"], y["eps"], y["yoy_pct"], y["change"]) for y in a["years"]] == [
        ("2023/12", 302.0, -26.0, -108.6093, "turned_to_loss"),
        ("2024/12", -26.0, 59.0, None, "turned_to_profit"),
        ("2025/12", 59.0, 258.0, 337.2881, "yoy")]
    assert a["status"] == "OK" and "2026/12" not in [y["period"] for y in a["years"]]   # estimates excluded
    short = G.annual_eps_growth(ANNUAL.loc[["2024/12", "2025/12"]])
    assert short["missing"] == {"2024/12": "prior_year_not_in_table", "years": "only_2_reported_years"}
    assert short["years"][0]["change"] is None and short["status"] == "PARTIAL"


def test_debt_ratio_quarter_then_annual_then_missing():
    d = G.debt_ratio(QUARTERLY, ANNUAL)
    assert (d["value_pct"], d["period"], d["view"], d["negative_equity"]) == (157.17, "2026/06", "quarterly", False)
    annual_only = G.debt_ratio(QUARTERLY.drop(columns=["debt_ratio"]), ANNUAL)
    assert (annual_only["value_pct"], annual_only["period"], annual_only["view"]) == (52.36, "2025/12", "annual")
    impaired = QUARTERLY.copy()
    impaired.loc["2026/06", "total_equity"] = -10.0
    neg = G.debt_ratio(impaired, ANNUAL)
    assert neg["value_pct"] is None and neg["negative_equity"] is True and neg["status"] == "OK"
    assert G.debt_ratio(None, None)["missing"] == {"debt_ratio": "not_collected"}
    assert G.debt_ratio(QUARTERLY.drop(columns=["debt_ratio"]), None)["missing"] == {"debt_ratio": "not_in_tables"}


def test_nothing_collected_is_missing_never_zero():
    facts = G.build(None, None, asof=ASOF, quarterly_reason="error_X")
    assert facts["status"] == "MISSING" and facts["quarterly"]["missing"] == {"quarterly": "error_X"}
    assert facts["annual_eps"]["missing"] == {"annual": "not_collected"}
    estimates_only = G.quarterly_growth(QUARTERLY[QUARTERLY["estimate"]])
    assert estimates_only == {"status": "MISSING", "missing": {"quarterly": "no_reported_quarter"}}
    json.dumps(G.build(QUARTERLY, ANNUAL, asof=ASOF))      # plain JSON types only (report_meta, ledger)


def test_prompt_block_is_flag_only(monkeypatch):
    facts = G.build(QUARTERLY, ANNUAL, asof=ASOF)
    assert G.prompt_block(facts) == ""
    monkeypatch.setenv("PRISM_BUY_GROWTH_FACTS", "true")
    ko = G.prompt_block(facts, language="ko")
    assert ko.startswith("\n### 📈 성장·재무 팩트")
    assert "영업이익 643억원 → 1,037억원 (+61.3%)" in ko and "EPS -21원 → 84원 (흑자전환, 증가율 없음)" in ko
    assert "직전 확정 분기 2026/03 영업이익 972억원" in ko and "매출 MISSING(직전 분기의 전년 동기가 표에 없음)" in ko
    assert "2023/12 302원 → -26원 (적자전환 -108.6%)" in ko and "2025/12 59원 → 258원 (+337.3%)" in ko
    assert "부채비율: 157.17% (2026/06 분기말)" in ko and "새 가점·감점·임계값·진입 조건이 아닙니다" in ko
    en = G.prompt_block(facts, language="en")
    assert "Operating income 643 (100M KRW) → 1,037 (100M KRW) (+61.3%)" in en and "Debt ratio: 157.17%" in en
    assert "turned to profit, no growth rate" in en and "no new bonus, penalty, threshold or entry condition" in en
    assert G.prompt_block(G.build(None, None, asof=ASOF)) == ""          # nothing collected: no block


class _Agent:
    pass


def test_collect_applies_the_look_ahead_guard(monkeypatch):
    monkeypatch.setenv("PRISM_BUY_GROWTH_FACTS", "true")
    agent = _Agent()
    agent._report_meta = {"018880": {"growth_facts": G.build(QUARTERLY, ANNUAL, asof="2026-10-23T01:00:01+00:00")}}
    assert G.collect(agent, "018880", now=NOW) == ""
    late = agent._buy_growth_facts["018880"]
    assert late["status"] == "MISSING" and late["missing"] == {"report": "collected_after_decision"}
    assert late["prompt_included"] is False
    agent._report_meta["018880"]["growth_facts"]["asof"] = ASOF
    block = G.collect(agent, "018880", now=NOW)
    assert block and agent._buy_growth_facts["018880"]["prompt_included"] is True
    assert G.collect(_Agent(), "018880", now=NOW) == ""        # no report meta


def test_ledger_records_the_block_without_prompt_or_decision_change(monkeypatch):
    sent = []
    monkeypatch.setattr(D, "emit_event", lambda name, **kw: sent.append(kw) or {"ok": 1})
    facts = G.build(QUARTERLY, ANNUAL, asof=ASOF)
    agent = _Agent()
    agent._report_meta = {"018880": {"growth_facts": facts}}

    def emit(decision_id):
        return D.emit_decision_inputs(agent, market="KR", ticker="018880", decision_id=decision_id, scenario={},
                                      current_price=140.0, decision="entry", source="t", now=NOW)

    # Not built by the prompt step: read straight from report_meta, flagged as not shown.
    payload = emit("d-g1")
    assert payload["buy_growth_facts"] == {**facts, "prompt_included": False}
    assert payload["trading_impact"] == "none" and payload["decision"] == "entry"
    assert G.collect(agent, "018880", now=NOW) == ""                      # flag off: ledger copy only
    assert emit("d-g2")["buy_growth_facts"]["prompt_included"] is False
    agent._report_meta["018880"]["growth_facts"] = {**facts, "asof": "2026-10-23T02:00:00+00:00"}
    agent._buy_growth_facts = {}
    assert emit("d-g3")["buy_growth_facts"]["missing"] == {"report": "collected_after_decision"}
    from observability.events import build_event
    event = build_event("decision_inputs.shadow_captured", service="s", attributes=sent[0]["attributes"])
    assert event["attributes"]["buy_growth_facts"]["debt_ratio"]["value_pct"] == 157.17
    us = D.emit_decision_inputs(agent, market="US", ticker="018880", decision_id="d-g4", scenario={},
                                current_price=1.0, decision="entry", source="t", now=NOW,
                                earnings_lookup=lambda *a: {"status": "MISSING"})
    assert "buy_growth_facts" not in us                                   # KR only


def test_existing_quarterly_guard_unchanged():
    assert D._quarterly_eps({}, NOW)["missing"] == {"latest_quarter": "not_in_report_meta"}
    late = D._quarterly_eps({"quarterly_eps": {"asof": "2026-10-23T02:00:00+00:00"}}, NOW)
    assert late == {"contract": "quarterly_eps_v1", "status": "MISSING",
                    "missing": {"latest_quarter": "collected_after_decision"}}
