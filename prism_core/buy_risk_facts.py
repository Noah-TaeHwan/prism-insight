"""BUY risk facts: the stop, R/R, momentum, stop-out and sector numbers the BUY rules ask for (KR/US).

The BUY instruction (cores/agents/trading_agents.py, prism-us/cores/agents/trading_agents.py) leaves the
model to derive these itself, and it chose No Entry on its own arithmetic before the deterministic gate
(cores/buy_gate.py) recomputed it. This block states the gate's own numbers instead: the effective
matrix row after the distribution-day step-down, the stop band of the 'primary_support' rule, the target
the R/R floor needs, the Step 3 items the trend facts already decided, recent stop-outs of the ticker and
the sector-cap count.

Facts only: no score, gate, threshold or order change. Anything not derivable is MISSING and says so.
Off by default (PRISM_BUY_RISK_FACTS); off keeps every BUY prompt byte-identical. No network I/O; collect()
only reads the agent's own SQLite history read-only.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date

CONTRACT = "buy_risk_facts_v1"
# BUY 'primary_support' check: a stop narrower than 50% of the matrix max stop is replaced by that
# floor. The constant lives only in the prompts; tests/test_buy_risk_facts.py pins it against them.
SUPPORT_FLOOR_FRACTION = 0.5
STOPOUT_WINDOW_DAYS = 10    # Step 1.6 "about 2 weeks", in trading days (= reentry_campaign.G1_SESSIONS)
QUICK_STOP_DAYS = 5
REPEAT_STOPS = 2            # Step 1.6 "2 or more stop-outs" (= reentry_campaign.G1_STOPS)
RECENT_EXITS_SHOWN = 5


def enabled():
    return os.getenv("PRISM_BUY_RISK_FACTS", "false").strip().lower() in {"1", "true", "yes", "on"}


def _gate():
    # cores/buy_gate.py by file path: inside the US process `cores` is prism-us/cores.
    from prism_core.reentry_campaign import _root_buy_gate
    return _root_buy_gate()


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _day(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value if not hasattr(value, "date") else value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def remember_trend_inputs(agent, ticker, *, price, volume=None, high52=None, dates=()):
    """Keep what the trend facts already decided, so this block repeats it instead of recomputing."""
    try:
        if not hasattr(agent, "_buy_risk_inputs"):
            agent._buy_risk_inputs = {}
        days = sorted({d for d in (_day(v) for v in list(dates)[-60:]) if d is not None})
        agent._buy_risk_inputs[ticker] = {
            "price": _positive(price),
            "volume_signal1": (volume or {}).get("signal1"),
            "near_high": (high52 or {}).get("near_high"),
            "trading_dates": [d.isoformat() for d in days],
        }
    except Exception:  # noqa: BLE001 - optional input must never fail the trend facts
        return


# --- 1. stop band and R/R target ------------------------------------------------------------
TICK = {"KR": 1.0, "US": 0.01}


def _to_tick(value, tick, up):
    steps = round(value / tick, 6)
    return round((math.ceil(steps) if up else math.floor(steps)) * tick, 6)


def _min_target(price, stop, rr_floor, tick):
    """Lowest tick price the gate's own R/R arithmetic accepts at this stop (no float-edge refusal)."""
    target = _to_tick(price + rr_floor * (price - stop), tick, up=True)
    while ((target - price) / price * 100.0) / ((price - stop) / price * 100.0) < rr_floor:
        target = round(target + tick, 6)
    return target


def stop_rr_facts(regime, trend_facts, price, tick=0.01):
    """Effective matrix row and its price levels, from cores/buy_gate.py's own table and step-down.

    Prices are rounded to the tick on the safe side: the max-stop price up (never wider than the max
    stop), the 50% floor price down (never narrower than the floor), the minimum target up."""
    gate = _gate()
    computed = gate.normalize_regime(regime)
    distribution = gate._distribution_from_text(trend_facts or "")
    effective, caution = gate.effective_buy_regime(computed, distribution)
    rule = gate.REGIME_RULES.get(effective or "")
    out = {"computed_regime": computed, "distribution_days": distribution, "distribution_step_down": caution,
           "distribution_step_down_at": gate._DISTRIBUTION_CAUTION,
           "effective_regime": effective, "price_basis": _positive(price)}
    if rule is None:
        return {**out, "status": "MISSING", "missing": {"effective_regime": "no_computed_regime"}}
    max_loss = float(rule["max_loss_pct"])
    floor_loss = max_loss * SUPPORT_FLOOR_FRACTION
    rr = float(rule["rr_floor"])
    out.update(max_loss_pct=max_loss, support_floor_loss_pct=floor_loss, rr_floor=rr,
               target_gain_at_max_stop_pct=round(rr * max_loss, 4),
               target_gain_at_floor_stop_pct=round(rr * floor_loss, 4))
    p = out["price_basis"]
    if p is None:
        return {**out, "status": "PARTIAL", "missing": {"price_basis": "no_trend_facts_price"}}
    max_stop = _to_tick(p * (1 - max_loss / 100), tick, up=True)
    floor_stop = _to_tick(p * (1 - floor_loss / 100), tick, up=False)
    if not 0 < max_stop <= floor_stop < p:
        return {**out, "status": "PARTIAL", "missing": {"price_levels": "price_below_tick_resolution"}}
    out.update(max_stop_price=max_stop, floor_stop_price=floor_stop,
               min_target_at_max_stop=_min_target(p, max_stop, rr, tick),
               min_target_at_floor_stop=_min_target(p, floor_stop, rr, tick))
    return {**out, "status": "OK"}


# --- 2. Step 3 momentum signals -------------------------------------------------------------
def momentum_facts(inputs, *, market):
    """Step 3 items 1-5: only 1 (volume) and 3 (52-week high) are computed, from the trend facts' verdicts."""
    inputs = inputs or {}
    volume = {"met": "MET", "not_met": "NOT_MET", "undetermined": "UNDETERMINED"}.get(inputs.get("volume_signal1"),
                                                                                     "MISSING")
    near = inputs.get("near_high")
    signals = {
        "volume_200pct": volume,
        # KR: investor flow is in the report only. US: the prompt does not evaluate it (scores 0).
        "investor_flow_3d": "NOT_EVALUATED" if market == "US" else "MISSING",
        "near_52w_high": "MISSING" if near is None else ("MET" if near else "NOT_MET"),
        "sector_uptrend": "MISSING",      # report section 4 judgement
        "box_breakout": "MISSING",        # report section 1-1 judgement
    }
    return {"signals": signals, "met_count": sum(v == "MET" for v in signals.values()),
            "missing_count": sum(v in ("MISSING", "UNDETERMINED") for v in signals.values())}


# --- 3. recent stop-outs --------------------------------------------------------------------
def stopout_facts(rows, trading_dates):
    """Stop-outs in the last STOPOUT_WINDOW_DAYS trading days from (buy_date, sell_date, profit_rate, exit_kind).

    Trading days come from the ticker's own daily frame. exit_kind NULL (legacy rows) is unknown, never
    'not a stop'; the Step 1.6 count condition then stays MISSING unless the known stops already decide it.
    """
    days = [d for d in (_day(v) for v in trading_dates or ()) if d is not None]
    if len(days) < STOPOUT_WINDOW_DAYS:
        return {"status": "MISSING", "missing": {"trading_dates": len(days)}}
    start = days[-STOPOUT_WINDOW_DAYS]
    exits = []
    for buy_date, sell_date, profit_rate, exit_kind in rows or ():
        sold, bought = _day(sell_date), _day(buy_date)
        if sold is None or sold < start:
            continue
        held = None
        if bought is not None and bought >= days[0]:
            held = sum(1 for d in days if bought < d <= sold)
        exits.append({"sell_date": sold.isoformat(), "exit_kind": exit_kind or None,
                      "profit_rate": round(float(profit_rate), 2) if profit_rate is not None else None,
                      "trading_days_held": held,
                      "within_quick_window": None if held is None else held <= QUICK_STOP_DAYS})
    exits.sort(key=lambda e: e["sell_date"], reverse=True)
    stops = [e for e in exits if e["exit_kind"] == "stop"]
    unknown = sum(1 for e in exits if e["exit_kind"] is None)
    if len(stops) >= REPEAT_STOPS:
        repeat = True
    elif len(stops) + unknown >= REPEAT_STOPS:
        repeat = None
    else:
        repeat = False
    return {"status": "OK", "window_start": start.isoformat(), "window_days": STOPOUT_WINDOW_DAYS,
            "stop_count": len(stops), "unknown_kind_count": unknown,
            "quick_stop_count": sum(1 for e in stops if e["within_quick_window"]),
            "repeat_stop_condition": repeat, "exits": exits[:RECENT_EXITS_SHOWN]}


# --- 4. sector cap and slot usage -----------------------------------------------------------
def sector_facts(sector, holding_scenarios, *, same_sector, max_same, ratio, min_for_ratio, allowed,
                 used_slots, slot_cap):
    """Counts as tracking.helpers.check_sector_diversity reads them; `allowed` is that function's verdict."""
    out = {"used_slots": round(float(used_slots), 2) if used_slots is not None else None, "slot_cap": slot_cap,
           "max_same_sector": max_same, "concentration_ratio": ratio, "ratio_min_holdings": min_for_ratio}
    if not sector or str(sector).strip().lower() == "unknown":
        return {**out, "status": "MISSING", "missing": {"sector": "unknown"}}
    sectors = []
    for raw in holding_scenarios or ():
        try:
            held = json.loads(raw).get("sector") if isinstance(raw, str) else None
        except (TypeError, ValueError, AttributeError):
            continue
        if held:
            sectors.append(held)
    same = sum(1 for held in sectors if same_sector(held, sector))
    return {**out, "status": "OK", "sector": sector, "same_sector_count": same, "sector_holdings": len(sectors),
            "cap_allows_new_entry": allowed}


def build(*, market, regime, trend_facts, inputs, stopout_rows, sector):
    """Full facts dict; each part fails to MISSING on its own."""
    inputs = inputs or {}
    facts = {"contract": CONTRACT, "market": market}
    for key, make in (("stop_rr", lambda: stop_rr_facts(regime, trend_facts, inputs.get("price"), TICK[market])),
                      ("momentum", lambda: momentum_facts(inputs, market=market)),
                      ("stopouts", lambda: stopout_facts(stopout_rows, inputs.get("trading_dates"))
                       if stopout_rows is not None else {"status": "MISSING", "missing": {"history": "unreadable"}}),
                      ("sector", lambda: sector if isinstance(sector, dict) else
                       {"status": "MISSING", "missing": {"holdings": "unreadable"}})):
        try:
            facts[key] = make()
        except Exception as error:  # noqa: BLE001
            facts[key] = {"status": "MISSING", "missing": {"error": type(error).__name__}}
    facts["status"] = "OK" if all(facts[k].get("status") == "OK" for k in ("stop_rr", "stopouts", "sector")) \
        else "PARTIAL"
    return facts


# --- prompt block ---------------------------------------------------------------------------
_LABELS = {
    "ko": {"MET": "충족", "NOT_MET": "미충족", "UNDETERMINED": "미확정(충족으로 세지 않음)", "MISSING": "결측"},
    "en": {"MET": "met", "NOT_MET": "not met", "UNDETERMINED": "undetermined (not counted as met)",
           "MISSING": "missing"},
}
_SIGNAL_NAMES = {
    "ko": {"volume_200pct": "1. 거래량 200%(개별 추세 팩트의 신호 1 판정)",
           "investor_flow_3d": "2. {actor} 3거래일 연속 순매수", "near_52w_high": "3. 52주 최고가 95% 이상(개별 추세 팩트의 2a (a))",
           "sector_uptrend": "4. 섹터 상승 추세(보고서 4)", "box_breakout": "5. 박스 상단 거래량 동반 돌파(보고서 1-1)"},
    "en": {"volume_200pct": "1. Volume 200% (the trend facts' signal 1 verdict)",
           "investor_flow_3d": "2. {actor} net buying 3 sessions", "near_52w_high": "3. Within 5% of the 52-week high (trend facts 2a (a))",
           "sector_uptrend": "4. Sector uptrend (report 4)", "box_breakout": "5. Box-top breakout on volume (report 1-1)"},
}
_FLOW_ACTOR = {("KR", "ko"): "외국인+기관", ("US", "ko"): "기관", ("KR", "en"): "Foreign + institutional",
               ("US", "en"): "Institutional"}
_EXIT_KIND = {"ko": {"stop": "손절", "trend_exit": "추세 이탈", "target": "목표", "ai": "AI 매도"},
              "en": {"stop": "stop-out", "trend_exit": "trend exit", "target": "target", "ai": "AI sell"}}


def render(facts, *, market, language="ko"):
    ko = language == "ko"
    lang = "ko" if ko else "en"
    miss = "결측" if ko else "missing"

    def money(value):
        return miss if value is None else (f"{value:,.0f}원" if market == "KR" else f"${value:,.2f}")

    def yn(value):
        if value is None:
            return miss
        return ("예" if value else "아니오") if ko else ("yes" if value else "no")

    lines = ["### 🧮 매수 위험 팩트 (결정론적 계산 · buy_risk_facts_v1)" if ko
             else "### 🧮 BUY risk facts (deterministic · buy_risk_facts_v1)"]
    s = facts.get("stop_rr") or {}
    if s.get("effective_regime"):
        step = (f"분산일 {s['distribution_days']}건({s['distribution_step_down_at']}건 이상)으로 신규 매수 한 단계 강등" if s.get("distribution_step_down")
                else f"분산일 {s['distribution_days'] if s.get('distribution_days') is not None else '줄 없음'} → 강등 없음") \
            if ko else (f"{s['distribution_days']} distribution days ({s['distribution_step_down_at']}+) step new buys down one level"
                        if s.get("distribution_step_down") else
                        f"distribution days {s['distribution_days'] if s.get('distribution_days') is not None else 'line absent'}"
                        " → no step-down")
        lines.append(
            f"- 적용 매트릭스 행: 시스템 국면 {s['computed_regime']}, {step} → {s['effective_regime']} 행(최대 손절폭 "
            f"-{s['max_loss_pct']:g}%, 손익비 floor {s['rr_floor']:g}). 최종 매수 점검과 같은 계산입니다." if ko else
            f"- Matrix row: system regime {s['computed_regime']}, {step} → {s['effective_regime']} row (max stop "
            f"-{s['max_loss_pct']:g}%, R/R floor {s['rr_floor']:g}). Same calculation as the final buy check.")
        p = s.get("price_basis")
        lines.append(
            f"- 손절 범위(기준가 {money(p)} = 개별 추세 팩트 현재가): 최대 손절폭 손절가 {money(s.get('max_stop_price'))}"
            f"(-{s['max_loss_pct']:g}%, 이보다 낮으면 최대 손절폭 초과) ~ primary_support 검증의 50% floor "
            f"{money(s.get('floor_stop_price'))}(-{s['support_floor_loss_pct']:g}%, 이보다 높으면 '손절가 설정'의 1·2번 절차 적용)"
            if ko else
            f"- Stop band (basis {money(p)} = trend facts current price): max-stop price {money(s.get('max_stop_price'))} "
            f"(-{s['max_loss_pct']:g}%; lower exceeds the max stop) to the primary_support 50% floor "
            f"{money(s.get('floor_stop_price'))} (-{s['support_floor_loss_pct']:g}%; higher triggers steps 1-2 of the stop rules)")
        lines.append(
            f"- 손익비 floor {s['rr_floor']:g}에 필요한 최소 목표가: 최대 손절폭 손절이면 {money(s.get('min_target_at_max_stop'))}"
            f"(+{s['target_gain_at_max_stop_pct']:g}%), 50% floor 손절이면 {money(s.get('min_target_at_floor_stop'))}"
            f"(+{s['target_gain_at_floor_stop_pct']:g}%). 필요 상승률 = floor × 손절폭입니다." if ko else
            f"- Minimum target for R/R floor {s['rr_floor']:g}: {money(s.get('min_target_at_max_stop'))} "
            f"(+{s['target_gain_at_max_stop_pct']:g}%) with the max stop, {money(s.get('min_target_at_floor_stop'))} "
            f"(+{s['target_gain_at_floor_stop_pct']:g}%) with the 50% floor stop. Required gain = floor × stop width.")
    else:
        lines.append("- 적용 매트릭스 행: 결측(시스템 국면 없음). 손절 범위·최소 목표가는 계산하지 않았습니다." if ko else
                     "- Matrix row: missing (no system regime); stop band and minimum target not computed.")

    m = facts.get("momentum") or {}
    if m.get("signals"):
        labels, names = _LABELS[lang], _SIGNAL_NAMES[lang]
        actor = _FLOW_ACTOR[(market, lang)]
        # US item 2 is "not evaluated, do not mention" in the US prompt: it stays on the ledger only.
        items = " / ".join(f"{names[k].format(actor=actor)}: {labels[v]}" for k, v in m["signals"].items()
                           if v != "NOT_EVALUATED")
        lines.append(
            f"- 3단계 모멘텀 신호(코드가 아는 항목만 판정): {items} → 확인된 충족 {m['met_count']}개, "
            f"결측·미확정 {m['missing_count']}개" if ko else
            f"- Step 3 momentum signals (only items the code knows): {items} → confirmed met {m['met_count']}, "
            f"missing/undetermined {m['missing_count']}")

    o = facts.get("stopouts") or {}
    if o.get("status") == "OK":
        if o["exits"]:
            def exit_text(e):
                held = miss if e["trading_days_held"] is None else e["trading_days_held"]
                kind = _EXIT_KIND[lang].get(e["exit_kind"], e["exit_kind"]) if e["exit_kind"] else \
                    ("유형 미기록" if ko else "kind unrecorded")
                rate = miss if e["profit_rate"] is None else f"{e['profit_rate']:+.1f}%"
                return (f"{e['sell_date']} {kind} {rate}(매수 후 {held}거래일)" if ko
                        else f"{e['sell_date']} {kind} {rate} ({held} trading days after buy)")
            listed = "; ".join(exit_text(e) for e in o["exits"])
        else:
            listed = "없음" if ko else "none"
        lines.append(
            f"- 이 종목 최근 {o['window_days']}거래일({o['window_start']}~) 매도 기록: {listed} → 손절(stop) "
            f"{o['stop_count']}회, 그중 매수 후 {QUICK_STOP_DAYS}거래일 이내 {o['quick_stop_count']}회, 유형 미기록 "
            f"{o['unknown_kind_count']}건 → 1.6단계 횟수 조건(약 2주 내 손절 {REPEAT_STOPS}회 이상): "
            f"{yn(o['repeat_stop_condition'])}(새 구조적 촉매 예외는 기존대로 보고서로 판단)" if ko else
            f"- This ticker's sells in the last {o['window_days']} trading days (from {o['window_start']}): {listed} → "
            f"stop-outs {o['stop_count']}, of which within {QUICK_STOP_DAYS} trading days of the buy "
            f"{o['quick_stop_count']}; kind unrecorded {o['unknown_kind_count']} → Step 1.6 count condition "
            f"({REPEAT_STOPS}+ stop-outs in ~2 weeks): {yn(o['repeat_stop_condition'])} (the new-catalyst "
            "exception is judged from the report as before)")
    else:
        lines.append("- 이 종목 최근 매도 기록: 결측(조회 불가이며 기록이 없다는 뜻이 아닙니다)" if ko else
                     "- This ticker's recent sells: missing (unknown, not 'none')")

    c = facts.get("sector") or {}
    usage = (f"실제 사용 비중 {c['used_slots']:.1f}슬롯 / 최대 {c['slot_cap']}" if ko else
             f"slot-weighted usage {c['used_slots']:.1f} / max {c['slot_cap']}") \
        if c.get("used_slots") is not None else ("실제 사용 비중 결측" if ko else "slot-weighted usage missing")
    if c.get("status") == "OK":
        lines.append(
            f"- 업종 한도: {c['sector']} 보유 {c['same_sector_count']}종목 / 업종당 {c['max_same_sector']}종목 한도"
            f"(보유 {c['ratio_min_holdings']}종목 이상이면 한 업종 {c['concentration_ratio'] * 100:.0f}% 한도도 적용, 현재 "
            f"{c['sector_holdings']}종목) → 시스템 업종 한도상 신규 진입 가능: {yn(c['cap_allows_new_entry'])}; {usage}"
            if ko else
            f"- Sector cap: {c['sector']} {c['same_sector_count']} held / {c['max_same_sector']} per sector (the "
            f"{c['concentration_ratio'] * 100:.0f}% one-sector cap also applies from {c['ratio_min_holdings']} holdings; "
            f"now {c['sector_holdings']}) → new entry allowed by the system sector cap: "
            f"{yn(c['cap_allows_new_entry'])}; {usage}")
    else:
        lines.append(f"- 업종 한도: 결측(업종 미확인); {usage}" if ko else f"- Sector cap: missing (sector unknown); {usage}")

    lines.append(
        "- 사용법: 기존 매트릭스·손절·손익비·3단계·1.6단계 규칙의 입력값일 뿐 새 가점·감점·진입 조건이 아닙니다. 같은 사실을 "
        "두 번 세지 마십시오. 목표가는 이 최소 목표가에 맞추지 말고 기존 규칙대로 근거로 먼저 정한 뒤 손익비를 평가하십시오. "
        "진입가가 기준가와 다르면 같은 비율로 다시 계산하십시오. 모멘텀 확인 개수는 하한값이며 트리거 자동 가산은 포함하지 "
        "않았습니다(결측 항목은 보고서로 판단). 업종 한도 판정은 시스템이 주문 단계에서 따로 적용하는 값이며, 포트폴리오 제약의 기존 규칙(동일 산업군 2개 이상 보유 시 rationale에 명시)은 그대로입니다."
        if ko else
        "- Use: inputs to the existing matrix, stop, R/R, Step 3 and Step 1.6 rules only; no new bonus, penalty or entry "
        "condition. Never count the same fact twice. Do not fit the target to this minimum: set it from evidence first as "
        "the target rules require, then evaluate R/R. If the entry price differs from the basis, rescale by the same "
        "percentages. The momentum count is a lower bound and excludes the trigger-type credit (judge missing items from "
        "the report). The sector-cap verdict is what the system applies separately at order time; the existing portfolio rule (state sector concentration in the rationale when 2+ names share the sector) is unchanged.")
    return "\n".join(lines) + "\n"


def prompt_block(facts, *, market, language="ko"):
    """'' unless PRISM_BUY_RISK_FACTS is on; otherwise the rendered block."""
    if not enabled() or not facts:
        return ""
    return "\n" + render(facts, market=market, language=language)


_HISTORY_TABLE = {"KR": "trading_history", "US": "us_trading_history"}


def collect(agent, ticker, *, market, language, regime, trend_facts, sector, holding_scenarios,
            sector_allowed, same_sector, ratio_min_holdings):
    """Build the facts, cache them for the decision ledger (agent._buy_risk_facts) and return the block.

    Read-only: the ticker's own sells from the history table under the agent's account scope (as
    check_sector_diversity scopes holdings). Returns '' when the flag is off or on any failure.
    """
    try:
        inputs = (getattr(agent, "_buy_risk_inputs", {}) or {}).get(ticker)
        account_key = agent._account_scope()[0]
        table = _HISTORY_TABLE[market]
        try:
            sql = f"SELECT buy_date, sell_date, profit_rate, exit_kind FROM {table} WHERE ticker = ?"  # nosec B608
            params = (ticker,)
            if account_key:
                sql, params = sql + " AND account_key = ?", (ticker, account_key)
            agent.cursor.execute(sql + " ORDER BY sell_date DESC LIMIT 50", params)  # nosemgrep
            rows = [tuple(row) for row in agent.cursor.fetchall()]
        except Exception:  # noqa: BLE001 - unreadable history stays MISSING
            rows = None
        try:
            from prism_core.micro_split_live import used_slots
            sector_part = sector_facts(sector, holding_scenarios, same_sector=same_sector,
                                       max_same=agent.MAX_SAME_SECTOR, ratio=agent.SECTOR_CONCENTRATION_RATIO,
                                       min_for_ratio=ratio_min_holdings, allowed=sector_allowed,
                                       used_slots=used_slots(holding_scenarios), slot_cap=agent.max_slots)
        except Exception:  # noqa: BLE001
            sector_part = None
        facts = build(market=market, regime=regime, trend_facts=trend_facts, inputs=inputs,
                      stopout_rows=rows, sector=sector_part)
        facts["prompt_included"] = enabled()
        if not hasattr(agent, "_buy_risk_facts"):
            agent._buy_risk_facts = {}
        agent._buy_risk_facts[ticker] = facts
        return prompt_block(facts, market=market, language=language)
    except Exception:  # noqa: BLE001 - optional input must never fail a BUY decision
        return ""
