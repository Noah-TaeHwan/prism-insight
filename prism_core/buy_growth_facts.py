"""BUY growth facts: O'Neil C (latest quarters) and A (annual EPS) plus the debt ratio, as numbers (KR).

The BUY instruction's CAN SLIM table asks for "recent quarterly EPS / revenue acceleration" (C) and
"multi-year EPS growth" (A), and its F1/F2 checks read operating profit and the debt ratio from report 2-1,
so the model derived every growth rate from prose. The report step already holds both views of WiseReport's
Financial Summary (cF1001): the quarterly frame behind the 2-1 quarterly table (#851, quarterly_eps_v1) and
the annual frame behind the earnings charts. This block states the growth rates from those same frames; it
adds no network fetch.

Facts only: no score, gate, threshold or order change. Reported periods only (consensus columns never enter).
A growth rate exists only on a positive base; a loss or zero base gets a sign-change label instead. Anything
not in the frames is MISSING with a reason, never 0. Off by default (PRISM_BUY_GROWTH_FACTS); off keeps every
BUY prompt byte-identical, while the decision ledger still records the dict (observation only).
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

CONTRACT = "buy_growth_facts_v1"
SOURCE = "wisereport_financial_summary"
QUARTER_METRICS = ("revenue", "operating_income", "net_income_controlling", "eps")
ANNUAL_YEARS = 3


def enabled():
    return os.getenv("PRISM_BUY_GROWTH_FACTS", "false").strip().lower() in {"1", "true", "yes", "on"}


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def growth(prior, now):
    """(yoy_pct, change): the same sign rules as decision_snapshot.quarterly_eps (quarterly_eps_v1)."""
    if prior is None or now is None:
        return None, None
    if prior > 0:
        return round((now / prior - 1) * 100, 4), ("yoy" if now > 0 else "turned_to_loss" if now < 0
                                                   else "fell_to_zero")
    if now > 0:
        return None, "turned_to_profit"
    return None, "loss_continued" if prior < 0 and now < 0 else "zero_base"


def _shift(period, months):
    year, month = (int(part) for part in str(period).split("/"))
    total = year * 12 + month - 1 + months
    return f"{total // 12}/{total % 12 + 1:02d}"


def _reported(frame):
    if frame is None or "estimate" not in frame:
        return None
    return frame[~frame["estimate"].astype(bool)]


def _cell(actual, period, column):
    if column not in actual or period not in actual.index:
        return None
    return _num(actual.at[period, column])


# --- C: latest reported quarter vs the prior-year quarter, and the quarter before -------------
def quarterly_growth(frame, reason=None):
    actual = _reported(frame)
    if actual is None:
        return {"status": "MISSING", "missing": {"quarterly": reason or "not_collected"}}
    if actual.empty:
        return {"status": "MISSING", "missing": {"quarterly": "no_reported_quarter"}}
    periods = set(actual.index)
    latest = str(actual.index[-1])
    prior, previous, previous_prior = _shift(latest, -12), _shift(latest, -3), _shift(latest, -15)
    out = {"period": latest, "prior_year_period": prior if prior in periods else None,
           "previous_period": previous if previous in periods else None,
           "previous_prior_year_period": previous_prior if previous_prior in periods else None,
           "statement_basis": frame.attrs.get("basis"), "metrics": {}}
    missing = {}
    for metric in QUARTER_METRICS:
        now, base = _cell(actual, latest, metric), _cell(actual, prior, metric)
        yoy, change = growth(base, now)
        p_now, p_base = _cell(actual, previous, metric), _cell(actual, previous_prior, metric)
        p_yoy, p_change = growth(p_base, p_now)
        out["metrics"][metric] = {
            "value": now, "prior_year_value": base, "yoy_pct": yoy, "change": change,
            "previous_quarter_value": p_now, "previous_quarter_prior_year_value": p_base,
            "previous_quarter_yoy_pct": p_yoy, "previous_quarter_change": p_change,
            "accelerating": None if yoy is None or p_yoy is None else yoy > p_yoy}
        if yoy is None:
            missing[f"{metric}.yoy_pct"] = ("prior_year_quarter_not_in_table" if prior not in periods else
                                            "not_reported" if change is None else "base_not_positive")
        if yoy is None or p_yoy is None:
            missing[f"{metric}.accelerating"] = (
                "latest_yoy_unavailable" if yoy is None else
                "previous_quarter_not_in_table" if previous not in periods else
                "previous_quarter_prior_year_not_in_table" if previous_prior not in periods else
                "previous_quarter_not_reported" if p_change is None else "previous_quarter_base_not_positive")
    return {**out, "status": "OK" if not missing else "PARTIAL", "missing": missing}


# --- A: annual EPS growth over the last reported fiscal years -------------------------------
def annual_eps_growth(frame):
    actual = _reported(frame)
    if actual is None:
        return {"status": "MISSING", "missing": {"annual": "not_collected"}}
    if "eps" not in actual or actual.empty:
        return {"status": "MISSING", "missing": {"annual": "no_reported_eps"}}
    years, missing = [], {}
    for period in [str(p) for p in actual.index[-ANNUAL_YEARS:]]:
        prior = _shift(period, -12)
        eps, base = _cell(actual, period, "eps"), _cell(actual, prior, "eps")
        yoy, change = growth(base, eps)
        years.append({"period": period, "eps": eps, "prior_period": prior, "prior_eps": base,
                      "yoy_pct": yoy, "change": change})
        if change is None:
            missing[period] = "prior_year_not_in_table" if prior not in actual.index else "not_reported"
    if len(years) < ANNUAL_YEARS:
        missing["years"] = f"only_{len(years)}_reported_years"
    return {"status": "OK" if not missing else "PARTIAL", "statement_basis": frame.attrs.get("basis"),
            "years": years, "missing": missing}


# --- debt ratio (F2 input) ------------------------------------------------------------------
def debt_ratio(quarterly, annual):
    """Latest reported quarter-end debt ratio, else the latest fiscal year-end; never an estimate."""
    for view, frame in (("quarterly", quarterly), ("annual", annual)):
        actual = _reported(frame)
        if actual is None or actual.empty or "debt_ratio" not in actual:
            continue
        period = str(actual.index[-1])
        value, equity = _cell(actual, period, "debt_ratio"), _cell(actual, period, "total_equity")
        out = {"period": period, "view": view, "statement_basis": frame.attrs.get("basis"), "total_equity": equity}
        if (equity is not None and equity <= 0) or (value is not None and value < 0):
            # Liabilities over non-positive equity is not a ratio; the sign is the fact.
            return {**out, "status": "OK", "value_pct": None, "negative_equity": True,
                    "missing": {"value_pct": "equity_not_positive"}}
        if value is not None:
            return {**out, "status": "OK", "value_pct": value, "negative_equity": False, "missing": {}}
    collected = quarterly is not None or annual is not None
    return {"status": "MISSING", "missing": {"debt_ratio": "not_in_tables" if collected else "not_collected"}}


def build(quarterly, annual, *, asof=None, quarterly_reason=None):
    """Full facts dict from the report step's frames; each part fails to MISSING on its own."""
    facts = {"contract": CONTRACT, "market": "KR", "source": SOURCE,
             "asof": asof or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "currency": "KRW", "unit_money": "100M KRW", "eps_basis": "reported_eps"}
    for key, make in (("quarterly", lambda: quarterly_growth(quarterly, quarterly_reason)),
                      ("annual_eps", lambda: annual_eps_growth(annual)),
                      ("debt_ratio", lambda: debt_ratio(quarterly, annual))):
        try:
            facts[key] = make()
        except Exception as error:  # noqa: BLE001
            facts[key] = {"status": "MISSING", "missing": {"error": type(error).__name__}}
    states = {facts[k]["status"] for k in ("quarterly", "annual_eps", "debt_ratio")}
    facts["status"] = "OK" if states == {"OK"} else "MISSING" if states == {"MISSING"} else "PARTIAL"
    return facts


# --- prompt block ---------------------------------------------------------------------------
_NAMES = {"ko": {"revenue": "매출", "operating_income": "영업이익", "net_income_controlling": "지배순이익", "eps": "EPS"},
          "en": {"revenue": "Revenue", "operating_income": "Operating income",
                 "net_income_controlling": "Controlling net income", "eps": "EPS"}}
_REASONS = {
    "ko": {"prior_year_quarter_not_in_table": "전년 동기 분기가 표에 없음", "not_reported": "값 없음",
           "base_not_positive": "기준값 0 이하", "latest_yoy_unavailable": "이번 분기 증가율 없음",
           "previous_quarter_not_in_table": "직전 분기가 표에 없음",
           "previous_quarter_prior_year_not_in_table": "직전 분기의 전년 동기가 표에 없음",
           "previous_quarter_not_reported": "직전 분기 값 없음",
           "previous_quarter_base_not_positive": "직전 분기 기준값 0 이하",
           "prior_year_not_in_table": "전년도가 표에 없음", "equity_not_positive": "자기자본 0 이하"},
    "en": {"prior_year_quarter_not_in_table": "prior-year quarter not in the table", "not_reported": "no value",
           "base_not_positive": "base not positive", "latest_yoy_unavailable": "no growth rate this quarter",
           "previous_quarter_not_in_table": "previous quarter not in the table",
           "previous_quarter_prior_year_not_in_table": "previous quarter's prior-year quarter not in the table",
           "previous_quarter_not_reported": "no previous-quarter value",
           "previous_quarter_base_not_positive": "previous-quarter base not positive",
           "prior_year_not_in_table": "prior year not in the table", "equity_not_positive": "equity not positive"},
}


def _miss(reason, lang):
    return f"MISSING({_REASONS[lang].get(reason, reason)})"


def _change_text(yoy, change, lang):
    ko = lang == "ko"
    if yoy is not None:
        label = {"yoy": "", "turned_to_loss": "적자전환 " if ko else "turned to loss ",
                 "fell_to_zero": "0으로 감소 " if ko else "fell to zero "}[change]
        return f"{label}{yoy:+.1f}%"
    return {"turned_to_profit": "흑자전환, 증가율 없음" if ko else "turned to profit, no growth rate",
            "loss_continued": "적자 지속, 증가율 없음" if ko else "loss continued, no growth rate",
            "zero_base": "기준값 0, 증가율 없음" if ko else "zero base, no growth rate"}[change]


def _amount(value, metric, lang):
    if value is None:
        return "MISSING"
    if metric == "eps":
        return f"{value:,.0f}원" if lang == "ko" else f"{value:,.0f} KRW"
    return f"{value:,.0f}억원" if lang == "ko" else f"{value:,.0f} (100M KRW)"


def render(facts, *, language="ko"):
    lang = "ko" if language == "ko" else "en"
    ko = lang == "ko"
    names = _NAMES[lang]
    q, a, d = facts.get("quarterly") or {}, facts.get("annual_eps") or {}, facts.get("debt_ratio") or {}
    lines = ["### 📈 성장·재무 팩트 (결정론적 계산 · buy_growth_facts_v1)" if ko
             else "### 📈 Growth and balance-sheet facts (deterministic · buy_growth_facts_v1)"]
    bases = f"{q.get('statement_basis') or 'MISSING'} / {a.get('statement_basis') or 'MISSING'}"
    lines.append(
        f"- 출처: WiseReport 재무요약 분기·연간 표의 확정치(추정치 제외), 수집 {facts.get('asof')}, 회계 기준(분기/연간) "
        f"{bases}, 금액 억원·EPS 원. 보고서 2-1 분기 실적 사전 수집 표와 같은 출처입니다." if ko else
        f"- Source: WiseReport Financial Summary quarterly and annual tables, reported periods only (estimates "
        f"excluded), collected {facts.get('asof')}, basis (quarterly/annual) {bases}, money in 100M KRW, EPS in KRW. "
        "Same source as the report 2-1 pre-collected quarterly table.")

    if q.get("metrics"):
        miss = q.get("missing") or {}

        def yoy_text(metric):
            m = q["metrics"][metric]
            reason = miss.get(f"{metric}.yoy_pct")
            rate = _change_text(m["yoy_pct"], m["change"], lang) if m["change"] else _miss(reason, lang)
            return (f"{names[metric]} {_amount(m['prior_year_value'], metric, lang)} → "
                    f"{_amount(m['value'], metric, lang)} ({rate})")

        prior = q.get("prior_year_period") or "MISSING"
        lines.append(
            f"- C 최근 확정 분기 {q['period']} vs 전년 동기 {prior}: "
            + "; ".join(yoy_text(m) for m in QUARTER_METRICS) if ko else
            f"- C latest reported quarter {q['period']} vs prior-year {prior}: "
            + "; ".join(yoy_text(m) for m in QUARTER_METRICS))

        def accel_text(metric):
            m = q["metrics"][metric]
            if m["accelerating"] is None:
                return f"{names[metric]} {_miss(miss.get(f'{metric}.accelerating'), lang)}"
            verdict = ("가속" if m["accelerating"] else "가속 아님") if ko else \
                ("accelerating" if m["accelerating"] else "not accelerating")
            return (f"{names[metric]} {m['yoy_pct']:+.1f}% vs 직전 분기 {m['previous_quarter_yoy_pct']:+.1f}% → {verdict}"
                    if ko else f"{names[metric]} {m['yoy_pct']:+.1f}% vs previous quarter "
                               f"{m['previous_quarter_yoy_pct']:+.1f}% → {verdict}")

        previous = q.get("previous_period") or "MISSING"
        op = q["metrics"]["operating_income"]["previous_quarter_value"]
        lines.append(
            f"- C 직전 확정 분기 {previous} 영업이익 {_amount(op, 'operating_income', lang)}. 전년 동기 대비 증가율 가속"
            f"(이번 분기 증가율 > 직전 분기 증가율): " + "; ".join(accel_text(m) for m in QUARTER_METRICS) if ko else
            f"- C previous reported quarter {previous} operating income {_amount(op, 'operating_income', lang)}. "
            "Year-over-year growth acceleration (this quarter's rate > the previous quarter's): "
            + "; ".join(accel_text(m) for m in QUARTER_METRICS))
    else:
        lines.append("- C 분기 실적: MISSING(분기 표 없음)" if ko else "- C quarterly results: MISSING (no quarterly table)")

    if a.get("years"):
        def year_text(y):
            if y["change"] is None:
                rate = _miss((a.get("missing") or {}).get(y["period"]), lang)
            else:
                rate = _change_text(y["yoy_pct"], y["change"], lang)
            return f"{y['period']} {_amount(y['prior_eps'], 'eps', lang)} → {_amount(y['eps'], 'eps', lang)} ({rate})"
        shown = "; ".join(year_text(y) for y in a["years"])
        short = (a.get("missing") or {}).get("years")
        tail = (f" (확정 회계연도 {len(a['years'])}개만 있음)" if ko else f" (only {len(a['years'])} reported years)") \
            if short else ""
        lines.append(f"- A 연간 EPS(최근 {ANNUAL_YEARS}개 확정 회계연도, 전년 대비): {shown}{tail}" if ko else
                     f"- A annual EPS (last {ANNUAL_YEARS} reported fiscal years, vs the prior year): {shown}{tail}")
    else:
        lines.append("- A 연간 EPS: MISSING(연간 표 없음)" if ko else "- A annual EPS: MISSING (no annual table)")

    if d.get("status") == "OK":
        when = (f"{d['period']} 분기말" if d["view"] == "quarterly" else f"{d['period']} 회계연도말") if ko else \
            (f"{d['period']} quarter-end" if d["view"] == "quarterly" else f"{d['period']} fiscal year-end")
        if d["negative_equity"]:
            lines.append(f"- 부채비율: 산출 불가 — 자기자본 0 이하({when})" if ko else
                         f"- Debt ratio: not computable — equity not positive ({when})")
        else:
            lines.append(f"- 부채비율: {d['value_pct']:.2f}% ({when})" if ko else
                         f"- Debt ratio: {d['value_pct']:.2f}% ({when})")
    else:
        lines.append("- 부채비율: MISSING(표에 없음)" if ko else "- Debt ratio: MISSING (not in the tables)")

    lines.append(
        "- 사용법: 기존 CAN SLIM C·A 판단과 F1(최근 2개 분기 영업이익)·F2(부채비율) 체크의 입력값일 뿐 새 가점·감점·임계값·"
        "진입 조건이 아닙니다. 보고서의 같은 수치를 두 번 세지 마십시오. 증가율은 양수 기준값에서만 계산했고 흑자전환·적자 "
        "지속은 증가율 대신 표시했습니다. MISSING은 이 출처에 값이 없다는 근거 상태일 뿐 0·미달·통과가 아니므로 보고서 등 "
        "다른 입력으로 기존 기준대로 판단하십시오. F2의 '업종 평균 이하' 조건은 이 블록에 없으며, 업종별 F2 기준이 함께 "
        "제공되면 그 기준이 부채비율보다 우선합니다." if ko else
        "- Use: inputs to the existing CAN SLIM C/A judgement and the F1 (operating income in the latest two quarters) "
        "and F2 (debt ratio) checks only; no new bonus, penalty, threshold or entry condition. Never count the same "
        "figure from the report twice. Growth rates exist only on a positive base; turnarounds and continued losses "
        "are labelled instead. MISSING is an evidence state for this source only, never 0, a fail or a pass: judge "
        "the item from the report and other inputs under the existing rules. F2's 'at or below the industry "
        "average' condition is not in this block, and a sector-specific F2 rule, when provided, takes precedence "
        "over the debt ratio.")
    return "\n".join(lines) + "\n"


def prompt_block(facts, *, language="ko"):
    """'' unless PRISM_BUY_GROWTH_FACTS is on and something was collected; otherwise the rendered block."""
    if not enabled() or not facts or facts.get("status") not in ("OK", "PARTIAL"):
        return ""
    return "\n" + render(facts, language=language)


def collect(agent, ticker, *, language="ko", now=None):
    """Read the report step's dict (look-ahead guarded), cache it for the decision ledger
    (agent._buy_growth_facts) and return the prompt block. '' when the flag is off or on any failure."""
    try:
        from observability.decision_inputs import report_block
        now = now or datetime.now(timezone.utc)
        meta = (getattr(agent, "_report_meta", None) or {}).get(ticker) or {}
        facts = report_block(meta, "growth_facts", CONTRACT, now, missing_key="report")
        block = prompt_block(facts, language=language)
        facts["prompt_included"] = bool(block)
        if not hasattr(agent, "_buy_growth_facts"):
            agent._buy_growth_facts = {}
        agent._buy_growth_facts[ticker] = facts
        return block
    except Exception:  # noqa: BLE001 - optional input must never fail a BUY decision
        return ""
