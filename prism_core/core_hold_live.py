"""Staged trust and core long-term holding, LIVE wiring (H-1023-exitfit E2-15 + H-1023-corehold).

Off unless CORE_HOLD_LIVE_ENABLED=true (one switch, one rollback). User approval 2026-10-23
("단계적 신뢰 실전 투입 ㄱㄱ", relayed by the buy session) after the gate in the PR.

Stages of a real holding (all on completed daily bars, written after the close by the daily
observer tools/run_core_hold_observer.py; e1 = the first buy fill, entry session = 1):
- before proof: today's rules unchanged (trailing stop, runner rule R5, hard stop, AI review);
- PROVEN: the first close with session >= 20, close >= 1.15 x e1 and close > MA50 > MA200. From the next
  session the trailing stop, R5 (MA20 / relative weakness), the loss-side MA50 exit, AI discretionary
  sells and AI stop changes are off. Sells: two consecutive closes below MA50 -> sold the next session
  (the trend-exit loop and the batch act on the pending exit), or the price at e1 (stop_loss = e1, the
  intraday hard stop); an official corporate event still sells;
- CORE: promotion (session >= 40, close >= 1.30 x e1, close > MA50 > MA200, MA200 rising over 20 sessions,
  close <= 1.4 x MA50, no exit pending), at most CAP per market (rank = steady-leader score, no
  replacement). Sells: a close below MA200 sets stop_loss to that day's low (the next session's low
  under it sells, intraday hard stop), or the price at e1. The regime is recorded at the promotion.
Sales record exit_kind proven_exit / core_exit (never the re-entry watch's stop-out).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from prism_core import core_hold as C

_LOG = logging.getLogger(__name__)
LIVE_VERSION = "core-hold-v1"
PROVEN = "PROVEN"
CORE = "CORE"
STATUSES = (PROVEN, CORE)
EXIT_KIND = {PROVEN: "proven_exit", CORE: "core_exit"}
PROVE_SESSIONS = 20
PROVE_GAIN = 0.15
PENDING_MA50 = "PROVEN_MA50_TWO_CLOSES"

_ROWS_SQL = {
    "KR": "SELECT id, ticker, buy_date, buy_price, scenario, stop_loss FROM stock_holdings",
    "US": "SELECT id, ticker, buy_date, buy_price, scenario, stop_loss FROM us_stock_holdings",
}
_ROW_SQL = {
    "KR": "SELECT scenario, stop_loss FROM stock_holdings WHERE id=?",
    "US": "SELECT scenario, stop_loss FROM us_stock_holdings WHERE id=?",
}
_WRITE_SQL = {
    "KR": "UPDATE stock_holdings SET scenario=?, stop_loss=? WHERE id=?",
    "US": "UPDATE us_stock_holdings SET scenario=?, stop_loss=? WHERE id=?",
}


def enabled(market):
    """CORE_HOLD_LIVE_ENABLED (default false) and the market in CORE_HOLD_LIVE_MARKETS (default KR,US)."""
    if os.getenv("CORE_HOLD_LIVE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    markets = {m.strip().upper() for m in os.getenv("CORE_HOLD_LIVE_MARKETS", "KR,US").split(",") if m.strip()}
    return str(market).upper() in markets


def _load(scenario):
    if isinstance(scenario, dict):
        return scenario
    try:
        loaded = json.loads(scenario or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def live_block(scenario):
    """The stored LIVE block (status PROVEN or CORE) of a scenario (dict or JSON), or None."""
    block = _load(scenario).get(C.SCENARIO_KEY)
    if isinstance(block, dict) and block.get("version") == LIVE_VERSION and block.get("status") in STATUSES:
        return block
    return None


def record(scenario):
    """The stored CORE block, or None."""
    block = live_block(scenario)
    return block if block and block["status"] == CORE else None


def view(stock, market):
    """The protected view for the sell guards (PROVEN or CORE, switch on), else None.

    ``exit`` is the pending close-confirmed exit of a PROVEN holding (sold by the trend-exit loop / batch)."""
    if not isinstance(stock, dict) or not enabled(market):
        return None
    block = live_block(stock.get("scenario"))
    if block is None:
        return None
    pending = (block.get("exit_pending") or {}).get("code") if block["status"] == PROVEN else None
    return {"kind": "core", "status": block["status"], "record": block, "phase": block["status"], "exit": pending}


def exit_kind(stock, market):
    """proven_exit / core_exit for a sale of a protected holding (any seller), else None (keep the caller's)."""
    protected = view(stock, market)
    return EXIT_KIND[protected["status"]] if protected else None


def exit_reason(protected, market, language):
    """The sell reason for a pending PROVEN exit (two closes below MA50)."""
    pending = protected["record"].get("exit_pending") or {}
    if language == "en":
        return (f"{PENDING_MA50}: staged-trust holding closed below the 50-day MA on two consecutive sessions "
                f"(last {pending.get('date')}); the rule sells the next session")
    return (f"{PENDING_MA50}: 단계적 신뢰 종목이 2거래일 연속 50일선 아래로 마감했습니다"
            f"(마지막 {pending.get('date')}). 규칙에 따라 다음 거래일에 매도합니다")


def stop_target(bars, first_entry):
    """CORE stop after the latest completed bar: e1, or max(e1, that day's low) after a close below MA200."""
    first = C._num(first_entry)
    if not bars or not first:
        return None, {}
    closes = C.closes_of(bars)
    i = len(bars) - 1
    m200 = C.ma(closes, i, 200)
    low = C._num(bars[i].get("low"))
    facts = {"date": bars[i]["date"], "close": closes[i], "low": low, "ma200": m200}
    if m200 is None or closes[i] is None:
        facts["armed"] = None                       # unknown: keep the breakeven stop, never guess a break
        return first, facts
    armed = closes[i] < m200 and low is not None
    facts["armed"] = armed
    return (max(first, low) if armed else first), facts


def proof(bars, index, *, first, entry_session):
    """The proof conditions on bar ``index``: True/False, None when unknown (never a proof)."""
    closes = C.closes_of(bars)
    close, m50, m200 = closes[index], C.ma(closes, index, 50), C.ma(closes, index, 200)
    sessions = C.session_number(bars, index, entry_session)
    if None in (close, m50, m200, sessions) or not first:
        return None
    return sessions >= PROVE_SESSIONS and close >= first * (1 + PROVE_GAIN) and close > m50 > m200


def two_closes_below_ma50(bars, *, after):
    """The latest two completed closes, both after the proof day, below their MA50."""
    closes = C.closes_of(bars)
    i = len(bars) - 1
    if i < 1 or str(bars[i - 1]["date"]) <= str(after):
        return False
    for j in (i - 1, i):
        m50 = C.ma(closes, j, 50)
        if m50 is None or closes[j] is None or not closes[j] < m50:
            return False
    return True


def _money(value, market, en):
    number = C._num(value)
    if number is None:
        return "unknown" if en else "미확인"
    if str(market).upper() == "US":
        return f"${number:,.2f}"
    return f"{number:,.0f} KRW" if en else f"{number:,.0f}원"


def prompt_block(stock, *, market, language):
    """Per-holding SELL appendix for a PROVEN or CORE holding; '' otherwise."""
    protected = view(stock, market)
    if protected is None:
        return ""
    if protected["status"] == CORE:
        return _core_prompt(stock, protected["record"], market=market, language=language)
    return _proven_prompt(stock, protected["record"], market=market, language=language)


def _proven_prompt(stock, block, *, market, language):
    en = language == "en"
    price = C._num(stock.get("current_price"))
    first = C._num(block.get("entry_first"))
    last = block.get("last") or {}
    gain = f"{(price / first - 1) * 100:+.1f}%" if price and first else ("unknown" if en else "미확인")
    if en:
        return (
            "\n\n### Staged-trust rule (takes precedence for this holding)\n"
            f"This holding proved itself on {block.get('proven_on')}: at least 20 sessions after the entry, a completed "
            "close 15% or more above the first buy, close above the 50-day MA above the 200-day MA. PRISM now gives it "
            "room to become a long winner instead of trailing it out on a pullback; at 40+ sessions and +30% it can be "
            "promoted to a core long-term holding.\n"
            f"- Facts: current price {_money(price, market, en)} ({gain} vs the first buy {_money(first, market, en)}), "
            f"50-day MA on the latest completed close {_money(last.get('ma50'), market, en)} ({last.get('date') or 'unknown'}), "
            f"stop {_money(stock.get('stop_loss'), market, en)} (the first buy price, breakeven).\n"
            "- The only valid sells, all executed by the system: (1) two consecutive completed closes below the 50-day "
            "MA (sold the next session), (2) the price falling to the first buy price (intraday hard stop), (3) an "
            "officially confirmed corporate event (Core-0).\n"
            "- Not sell reasons for this holding: target reached, overheating, a short-term surge or RSI, slowing "
            "momentum, a single close below the 20-day or 50-day MA, the Tier-3 trend-weakness composite, a "
            "trailing-stop break, weakness against the index, a market-regime change, holding period or time review, "
            "sector weakness, a pullback from the peak. The stored BUY scenario sell_triggers and the matching parts of "
            "the shared SELL instruction do not apply to this holding. A should_sell=true for such a reason is turned "
            "into a hold by the system. Keep should_sell=false and write the hold rationale.\n"
            "- The system manages the stop and target. Return portfolio_adjustment.needed=false with new_stop_loss "
            "and new_target_price null; any adjustment is ignored.\n"
            "- For a micro-split holding the next_session_add_plan request above stays valid: a proven holding is a "
            "position to build up. Its sentence 'the sell decision, stop and portfolio_adjustment rules are "
            "unchanged' is replaced by this rule for this holding.\n")
    return (
        "\n\n### 단계적 신뢰 규칙 (이 종목에 우선 적용)\n"
        f"이 종목은 {block.get('proven_on')}에 검증을 통과했습니다. 매수 후 20거래일 이상, 확정 종가가 최초 매수가 대비 "
        "+15% 이상, 종가가 200일선 위의 50일선 위에 있는 조건을 모두 충족했습니다. PRISM은 이제 눌림에 trailing stop으로 "
        "털지 않고 크게 갈 여지를 줍니다. 40거래일 이상·+30%가 되면 장기 보유 종목으로 승격될 수 있습니다.\n"
        f"- 현재 사실: 현재가 {_money(price, market, en)}(최초 매수가 {_money(first, market, en)} 대비 {gain}), 직전 확정 "
        f"종가 기준 50일선 {_money(last.get('ma50'), market, en)}({last.get('date') or '미확인'}), 손절가 "
        f"{_money(stock.get('stop_loss'), market, en)}(최초 매수가, 본전).\n"
        "- 매도가 유효한 경우는 다음뿐이며 모두 시스템이 실행합니다. (1) 확정 종가가 2거래일 연속 50일선 아래로 마감한 "
        "경우(다음 거래일 매도), (2) 가격이 최초 매수가(본전)까지 내려간 경우(장중 하드스탑), (3) 공식 확인된 법인 "
        "이벤트(핵심-0).\n"
        "- 이 종목에는 다음이 매도 사유가 아닙니다: 목표가 도달, 과열·단기 급등·RSI, 모멘텀 둔화, 20일선·50일선 1회 "
        "이탈, 3단계 추세 약화 복합 조건, trailing stop 이탈, 지수 대비 약세, 시장 국면 변화, 보유 기간·시간 점검, 업종 "
        "약세, 고점 대비 눌림. 저장된 매수 시나리오의 sell_triggers와 공유 매도 지침의 해당 항목은 이 종목에 적용하지 "
        "않습니다. 이런 이유로 should_sell=true를 내도 시스템이 보유로 바꿉니다. should_sell은 false로 두고 보유 근거를 "
        "쓰십시오.\n"
        "- 손절가와 목표가는 시스템이 관리합니다. portfolio_adjustment는 needed=false, new_stop_loss와 "
        "new_target_price는 null로 쓰십시오. 조정을 써도 반영되지 않습니다.\n"
        "- 초분할 보유라면 위의 증액 계획(next_session_add_plan) 요청은 그대로 유효합니다. 검증된 종목은 비중을 키울 "
        "대상입니다. 증액 블록의 '매도 판단, 손절가, portfolio_adjustment 기준은 바뀌지 않습니다' 문장은 이 종목에 한해 "
        "이 규칙으로 대체됩니다.\n")


def _core_prompt(stock, block, *, market, language):
    en = language == "en"
    last = block.get("last") or {}
    price = C._num(stock.get("current_price"))
    first = C._num(block.get("entry_first"))
    gain = f"{(price / first - 1) * 100:+.1f}%" if price and first else ("unknown" if en else "미확인")
    stop = _money(stock.get("stop_loss"), market, en)
    if en:
        armed = ("armed: the latest completed close was below the 200-day MA, so the stop is that day's low"
                 if last.get("armed") else "the first buy price (breakeven)")
        return (
            "\n\n### Core long-term holding rule (takes precedence for this holding)\n"
            f"This holding was promoted to a core long-term holding on {block.get('promoted_on')}: at least 40 sessions "
            "after the entry, +30% or more, close above the 50-day MA above a rising 200-day MA, not overextended. "
            "PRISM keeps one or two such long uptrends per market and does not sell them before the 200-day line "
            "breaks; drawdowns of 20-30% from the peak are expected while it is held.\n"
            f"- Facts: current price {_money(price, market, en)} ({gain} vs the first buy "
            f"{_money(first, market, en)}), 200-day MA on the latest completed close {_money(last.get('ma200'), market, en)} "
            f"({last.get('date') or 'unknown'}), stop {stop} ({armed}).\n"
            "- The only valid sells, all executed by the system: (1) a completed close below the 200-day MA followed "
            "by a session whose price falls under that close's daily low (the intraday hard stop sells at the stop "
            "set to that low), (2) the price falling to the first buy price (breakeven, intraday hard stop), "
            "(3) an officially confirmed corporate event (Core-0).\n"
            "- Not sell reasons for this holding: target reached, overheating, a short-term surge or RSI, slowing "
            "momentum, a 20-day or 50-day MA break, the Tier-3 trend-weakness composite, a trailing-stop break, "
            "weakness against the index, a market-regime change, holding period or time review, sector weakness, a "
            "drawdown from the peak. The stored BUY scenario sell_triggers and the matching parts of the shared SELL "
            "instruction do not apply to this holding. A should_sell=true for such a reason is turned into a hold by "
            "the system. Keep should_sell=false and write the hold rationale.\n"
            "- The system manages the stop and target. Return portfolio_adjustment.needed=false with new_stop_loss "
            "and new_target_price null; any adjustment is ignored.\n"
            "- For a micro-split holding the next_session_add_plan request above stays valid: a core holding is a "
            "position to build up. Its sentence 'the sell decision, stop and portfolio_adjustment rules are "
            "unchanged' is replaced by this rule for this holding.\n")
    armed = ("설정됨: 직전 확정 종가가 200일선 아래로 마감해 손절가가 그날 저가입니다" if last.get("armed")
             else "최초 매수가(본전)")
    return (
        "\n\n### 장기 보유(코어) 규칙 (이 종목에 우선 적용)\n"
        f"이 종목은 {block.get('promoted_on')}에 장기 보유 종목으로 승격됐습니다. 매수 후 40거래일 이상, +30% 이상, "
        "종가가 상승 중인 200일선 위의 50일선 위에 있고 과열이 아닌 조건을 모두 충족했습니다. PRISM은 시장마다 이런 "
        "장기 우상향 종목을 한두 개 붙들고 200일선 이탈 전에는 팔지 않습니다. 보유 중 고점 대비 20~30% 하락은 예상된 "
        "범위입니다.\n"
        f"- 현재 사실: 현재가 {_money(price, market, en)}(최초 매수가 {_money(first, market, en)} 대비 {gain}), 직전 "
        f"확정 종가 기준 200일선 {_money(last.get('ma200'), market, en)}({last.get('date') or '미확인'}), 손절가 {stop}"
        f"({armed}).\n"
        "- 매도가 유효한 경우는 다음뿐이며 모두 시스템이 실행합니다. (1) 확정 종가가 200일선 아래로 마감한 뒤 다음 "
        "거래일에 그날 저가 아래로 내려간 경우(손절가를 그 저가로 둔 장중 하드스탑이 매도), (2) 가격이 최초 매수가"
        "(본전)까지 내려간 경우(장중 하드스탑), (3) 공식 확인된 법인 이벤트(핵심-0).\n"
        "- 이 종목에는 다음이 매도 사유가 아닙니다: 목표가 도달, 과열·단기 급등·RSI, 모멘텀 둔화, 20일선·50일선 이탈, "
        "3단계 추세 약화 복합 조건, trailing stop 이탈, 지수 대비 약세, 시장 국면 변화, 보유 기간·시간 점검, 업종 약세, "
        "고점 대비 하락. 저장된 매수 시나리오의 sell_triggers와 공유 매도 지침의 해당 항목은 이 종목에 적용하지 "
        "않습니다. 이런 이유로 should_sell=true를 내도 시스템이 보유로 바꿉니다. should_sell은 false로 두고 보유 근거를 "
        "쓰십시오.\n"
        "- 손절가와 목표가는 시스템이 관리합니다. portfolio_adjustment는 needed=false, new_stop_loss와 "
        "new_target_price는 null로 쓰십시오. 조정을 써도 반영되지 않습니다.\n"
        "- 초분할 보유라면 위의 증액 계획(next_session_add_plan) 요청은 그대로 유효합니다. 장기 보유 종목은 비중을 "
        "키울 대상입니다. 증액 블록의 '매도 판단, 손절가, portfolio_adjustment 기준은 바뀌지 않습니다' 문장은 이 "
        "종목에 한해 이 규칙으로 대체됩니다.\n")


def _now():
    return datetime.now(timezone.utc)


def _emit(event_type, *, market, ticker, row_id, scenario, attributes):
    from observability.events import emit_event
    from prism_core.trace_ids import decision_of, trade_trace
    position = f"legacy:{market}:{row_id}" if row_id is not None else None
    decision = decision_of(scenario) if scenario else None
    emit_event(event_type, service=f"prism-{market.lower()}-core-hold", market=market, ticker=ticker,
               position_id=position, decision_id=decision,
               trace_id=trade_trace(market, decision_id=decision, position_id=position, ticker=ticker),
               attributes=attributes)


def _write(conn, market, row_id, mutate):
    """Fresh read under BEGIN IMMEDIATE; ``mutate(block, stop) -> (block, stop) | None``. Returns
    (old block, new block, old stop, new stop, scenario json) or None when nothing changed."""
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = conn.execute(_ROW_SQL[market], (row_id,)).fetchone()
        if fresh is None:
            conn.rollback()
            return None
        scenario = _load(fresh[0])
        old_block = scenario.get(C.SCENARIO_KEY) if isinstance(scenario.get(C.SCENARIO_KEY), dict) else None
        old_stop = C._num(fresh[1])
        out = mutate(live_block(scenario), old_stop)
        if out is None:
            conn.rollback()
            return None
        block, stop = out
        same_stop = old_stop is not None and stop is not None and abs(old_stop - stop) <= abs(stop) * 1e-9
        if block == old_block and same_stop:
            conn.rollback()
            return None
        scenario[C.SCENARIO_KEY] = block
        text = json.dumps(scenario, ensure_ascii=False)
        conn.execute(_WRITE_SQL[market], (text, stop if stop is not None else fresh[1], row_id))
        conn.commit()
        return old_block, block, old_stop, stop, text
    except Exception:
        conn.rollback()
        raise


def apply_live(conn, market, bars_by_key, *, regimes=None, now=None, alert=None):
    """After the close: proof, pending PROVEN exits, promotion (cap) and CORE stops. Returns actions.

    ``bars_by_key``: completed daily bars per "ticker|buy minute"; ``regimes``: callable(date) -> the
    logged regime dict (or None), recorded at each promotion.
    """
    market = str(market).upper()
    now = now or _now()
    actions = []
    if not enabled(market):
        return actions
    from prism_core import runner_hold as R
    rows = [dict(zip(("id", "ticker", "buy_date", "buy_price", "scenario", "stop_loss"), r))
            for r in conn.execute(_ROWS_SQL[market]).fetchall()]
    state = {}
    for row in rows:
        bars = bars_by_key.get(f"{row['ticker']}|{str(row['buy_date'])[:16]}") or []
        try:
            session = R.entry_session(market, row["buy_date"]).isoformat()
        except (TypeError, ValueError):
            continue
        first = R.entry_reference(row["scenario"], row["buy_price"])
        if not bars or not first:
            continue                                     # MISSING: nothing changes on unknown bars
        state[row["id"]] = {"row": row, "bars": bars, "session": session, "first": first,
                            "i": len(bars) - 1, "block": live_block(row["scenario"])}

    def act(kind, row, written, **extra):
        old_block, block, old_stop, stop, text = written
        attributes = {"contract": LIVE_VERSION, "status": block.get("status"), "stop_loss": stop,
                      "previous_stop_loss": old_stop, "entry_first": block.get("entry_first"),
                      "last": block.get("last"), **extra}
        actions.append({"action": kind, "ticker": row["ticker"], "row_id": row["id"], **attributes})
        try:
            _emit(f"core_hold.{kind}", market=market, ticker=row["ticker"], row_id=row["id"], scenario=text,
                  attributes=attributes)
        except Exception as error:  # noqa: BLE001 - an event never undoes a written stop
            _LOG.warning("[CORE_HOLD][%s] %s event failed: %s", market, row["ticker"], type(error).__name__)
        if alert and kind in ("proven", "promoted", "stop_armed", "exit_pending"):
            alert(_alert_text(market, row["ticker"], kind, block, stop, old_stop))

    # 1. proof (first close meeting it) and 2. a pending PROVEN exit (two closes below MA50 after the proof)
    for s in state.values():
        row, bars, i = s["row"], s["bars"], s["i"]
        closes = C.closes_of(bars)
        last = {"date": bars[i]["date"], "close": closes[i], "ma50": C.ma(closes, i, 50), "ma200": C.ma(closes, i, 200)}
        if s["block"] is None and proof(bars, i, first=s["first"], entry_session=s["session"]):
            block = {"version": LIVE_VERSION, "status": PROVEN, "proven_on": bars[i]["date"],
                     "proven_at": now.isoformat(), "entry_first": s["first"], "contract": C.VERSION, "last": last}
            written = _write(conn, market, row["id"], _set_new(block, s["first"]))
            if written:
                s["block"] = written[1]
                act("proven", row, written)
        elif s["block"] and s["block"]["status"] == PROVEN and not s["block"].get("exit_pending"):
            if two_closes_below_ma50(bars, after=s["block"]["proven_on"]):
                written = _write(conn, market, row["id"], _set_pending(last))
                if written:
                    s["block"] = written[1]
                    act("exit_pending", row, written)
            else:                                        # keep the prompt facts current
                written = _write(conn, market, row["id"], _set_last(last))
                if written:
                    s["block"] = written[1]
    # 3. promotion: PROVEN, no exit pending, not skipped, eligible today; CAP per market by ticker
    held = {s["row"]["ticker"] for s in state.values() if s["block"] and s["block"]["status"] == CORE}
    held |= {row["ticker"] for row in rows if record(row["scenario"])}
    candidates = {}
    for s in state.values():
        block = s["block"]
        if (not block or block["status"] != PROVEN or block.get("exit_pending") or block.get("promotion_skipped")
                or s["row"]["ticker"] in held):
            continue
        view_ = C.promotion(s["bars"], s["i"], entry=s["first"], entry_session=s["session"])
        if view_["eligible"]:
            share, ret = C.rank_inputs(s["bars"], s["i"])
            candidates.setdefault(s["row"]["ticker"], {"ticker": s["row"]["ticker"], "share": share, "ret": ret,
                                                       "view": view_, "states": []})["states"].append(s)
    ranked = C.rank(list(candidates.values()))
    free = max(C.CAP - len(held), 0)
    for n, cand in enumerate(ranked):
        seat = n < free
        for s in cand["states"]:
            row, view_ = s["row"], cand["view"]
            regime = regimes(view_["date"]) if callable(regimes) else None
            if seat:
                target, facts = stop_target(s["bars"], s["first"])
                changes = {"status": CORE, "promoted_on": view_["date"], "promoted_at": now.isoformat(),
                           "regime": regime, "rank": {"share": cand["share"], "ret": cand["ret"]},
                           "view": {k: view_[k] for k in ("sessions", "gain", "close", "ma50", "ma200", "ma200_lag")},
                           "last": facts}
            else:
                target, changes = None, {"promotion_skipped": view_["date"]}
            written = _write(conn, market, row["id"], _promote(changes, target))
            if written:
                s["block"] = written[1]
                act("promoted" if seat else "cap_skipped", row, written, regime=regime)
    # 4. CORE stops (first buy, or that day's low after a close below MA200)
    for s in state.values():
        if not s["block"] or s["block"]["status"] != CORE:
            continue
        target, facts = stop_target(s["bars"], s["first"])
        if target is None:
            continue
        written = _write(conn, market, s["row"]["id"], _restop(facts, target))
        if written and (written[2] is None or abs(written[2] - target) > target * 1e-9):
            act("stop_armed" if facts.get("armed") else "stop_set", s["row"], written)
    return actions


# Mutators for _write: (current LIVE block or None, current stop) -> (new block, new stop) or None (no change).
def _set_new(block, first):
    return lambda cur, stop: None if cur is not None else (block, first)


def _set_pending(last):
    def mutate(cur, stop):
        if cur is None or cur["status"] != PROVEN or cur.get("exit_pending"):
            return None
        return dict(cur, last=last, exit_pending={"code": PENDING_MA50, "date": last["date"]}), stop
    return mutate


def _set_last(last):
    return lambda cur, stop: None if cur is None or cur["status"] != PROVEN else (dict(cur, last=last), stop)


def _promote(changes, target):
    def mutate(cur, stop):
        if cur is None or cur["status"] != PROVEN:
            return None
        return dict(cur, **changes), (stop if target is None else target)
    return mutate


def _restop(facts, target):
    return lambda cur, stop: None if cur is None or cur["status"] != CORE else (dict(cur, last=facts), target)


def _alert_text(market, ticker, kind, block, stop, old_stop):
    name = {"KR": "한국", "US": "미국"}.get(market, market)
    if kind == "proven":
        return (f"[단계적 신뢰] {name} {ticker} 검증 통과({block.get('proven_on')}). 손절가 {old_stop} → {stop}(최초 매수가). "
                "이제 50일선 2일 연속 이탈 또는 본전에서만 팝니다.")
    if kind == "exit_pending":
        return (f"[단계적 신뢰] {name} {ticker} 50일선 아래 2일 연속 마감({(block.get('exit_pending') or {}).get('date')}). "
                "다음 거래일에 매도합니다.")
    if kind == "promoted":
        regime = (block.get("regime") or {}).get("regime") or "미확인"
        return (f"[장기 보유] {name} {ticker} 승격({block.get('promoted_on')}, 국면 {regime}). 손절가 {stop}(최초 매수가). "
                "200일선 이탈 확인 전에는 다른 매도를 막습니다.")
    return (f"[장기 보유] {name} {ticker} 200일선 아래 마감({(block.get('last') or {}).get('date')}). 손절가 {old_stop} → "
            f"{stop}(그날 저가). 다음 거래일 이 아래로 내려가면 하드스탑이 매도합니다.")
