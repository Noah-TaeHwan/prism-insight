"""Core long-term holding, LIVE wiring (H-1023-corehold). Off unless CORE_HOLD_LIVE_ENABLED=true.

User decisions 2026-10-23: one or two Coca-Cola-like long uptrends per market are not sold before
they break the 200-day line; the trailing stop stays in force before promotion; the breakeven stop
is the first buy (as the runner rule); no bear-market guard yet (the regime is recorded at each
promotion). LIVE needs the user's explicit approval; this module does nothing while the flag is off.

Pieces:
- the daily observer (tools/run_core_hold_observer.py) promotes after the close and manages the stop:
  ``apply_live`` writes ``scenario["core_hold"]`` and ``stop_loss`` (fresh read under BEGIN IMMEDIATE);
  the stop is the first buy, or that day's low after a completed close below the 200-day MA, so the
  intraday hard stop (tools/hardstop_seller.py) executes both core exits the next session;
- ``view`` marks a core row protected for the sell guards (prism_core/runner_hold_live.py): the AI
  sell, the trend-exit loop (trailing, MA50, target), the runner exits and stop adjustments are
  blocked; the hard stop and an official corporate event still sell;
- ``exit_kind`` records such a sale as ``core_exit`` (not a stop-out for the re-entry watch);
- ``prompt_block`` is the per-holding SELL appendix (non-core prompts stay byte-identical).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from prism_core import core_hold as C

_LOG = logging.getLogger(__name__)
LIVE_VERSION = "core-hold-v1"
CORE = "CORE"
EXIT_KIND = "core_exit"

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


def record(scenario):
    """The stored LIVE core block (status CORE) of a scenario (dict or JSON), or None."""
    block = _load(scenario).get(C.SCENARIO_KEY)
    if isinstance(block, dict) and block.get("version") == LIVE_VERSION and block.get("status") == CORE:
        return block
    return None


def view(stock, market):
    """A protected-position view for the sell guards when the row is a LIVE core holding, else None."""
    if not isinstance(stock, dict) or not enabled(market):
        return None
    block = record(stock.get("scenario"))
    if block is None:
        return None
    return {"kind": "core", "record": block, "phase": CORE, "exit": None}


def exit_kind(stock, market):
    """``core_exit`` for a sale of a LIVE core holding (any seller), else None (keep the caller's kind)."""
    return EXIT_KIND if view(stock, market) else None


def stop_target(bars, first_entry):
    """(stop, facts) after the latest completed bar: the first buy, or max(first buy, that day's low)
    after a close below the 200-day MA (the next session's low under it sells). None when unknown."""
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


def _money(value, market, en):
    number = C._num(value)
    if number is None:
        return "unknown" if en else "미확인"
    if str(market).upper() == "US":
        return f"${number:,.2f}"
    return f"{number:,.0f} KRW" if en else f"{number:,.0f}원"


def prompt_block(stock, *, market, language):
    """Per-holding SELL appendix for a LIVE core holding; '' otherwise."""
    core = view(stock, market)
    if core is None:
        return ""
    block, en = core["record"], language == "en"
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


def apply_live(conn, market, results, bars_by_key, *, now=None, alert=None):
    """Promote LIVE core holdings and set their stops after the close. Returns a list of actions.

    ``results``: tools/run_core_hold_observer.py evaluations (variant a = promotion while held);
    ``bars_by_key``: completed bars per result key. Promotion needs an open row, a promotion with no
    core exit yet and a free slot (CAP minus core rows already held); same-day order = the rank score.
    """
    market = str(market).upper()
    now = now or _now()
    actions = []
    if not enabled(market):
        return actions
    from prism_core import runner_hold as R
    rows = [dict(zip(("id", "ticker", "buy_date", "buy_price", "scenario", "stop_loss"), r))
            for r in conn.execute(_ROWS_SQL[market]).fetchall()]

    def key_of(row):
        return f"{row['ticker']}|{str(row['buy_date'])[:16]}"

    held_core = {row["ticker"] for row in rows if record(row["scenario"])}
    decided = {row["ticker"] for row in rows if _decided(row["scenario"])}   # core, or skipped for good
    free = C.CAP - len(held_core)
    by_key = {r["key"]: r for r in results}
    candidates = [r for r in results
                  if r.get("open") and r.get("a") and not r["a"].get("exit") and r["ticker"] not in decided]
    ordered = []
    for day in sorted({r["a"]["date"] for r in candidates}):
        same = [r for r in candidates if r["a"]["date"] == day]
        ordered += [c["r"] for c in C.rank([{"ticker": r["ticker"], "share": r["a"].get("share"),
                                              "ret": r["a"].get("ret"), "r": r} for r in same])]
    seen, unique = set(), []
    for r in ordered:                                   # one slot per ticker (rows of several accounts)
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique.append(r)
    promote = {r["ticker"]: r for r in unique[:max(free, 0)]}
    for r in unique[max(free, 0):]:                     # slots full on its promotion day: skipped for good
        skipped = _mark_skipped(conn, market, rows, r, now)
        actions.append({"action": "cap_full", "ticker": r["ticker"], "key": r["key"], "rows": skipped})
        try:
            _emit("core_hold.cap_skipped", market=market, ticker=r["ticker"], row_id=None, scenario=None,
                  attributes={"contract": LIVE_VERSION, "date": r["a"]["date"], "held_core": sorted(held_core)})
        except Exception as error:  # noqa: BLE001
            _LOG.warning("[CORE_HOLD][%s] %s event failed: %s", market, r["ticker"], type(error).__name__)
    for row in rows:
        key = key_of(row)
        block = record(row["scenario"])
        if block is None and row["ticker"] not in promote:
            continue
        result = by_key.get(key) if block is None and (by_key.get(key) or {}).get("a") else promote.get(row["ticker"])
        first = R.entry_reference(row["scenario"], row["buy_price"])
        target, facts = stop_target(bars_by_key.get(key) or [], first)
        if target is None:
            continue
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            fresh = conn.execute(_ROW_SQL[market], (row["id"],)).fetchone()
            if fresh is None:
                conn.rollback()
                continue
            scenario = _load(fresh[0])
            current = record(scenario)
            promoted = current is None
            if promoted:
                cand = result["a"]
                current = {"version": LIVE_VERSION, "status": CORE, "promoted_on": cand["date"],
                           "promoted_at": now.isoformat(), "entry_first": first,
                           "entry_avg": result.get("entry_avg"), "regime": cand.get("regime"),
                           "rank": {"share": cand.get("share"), "ret": cand.get("ret")},
                           "view": cand.get("view"), "contract": C.VERSION}
            updated = dict(current, last=facts, stop=target)
            old_stop = C._num(fresh[1])
            if updated == current and old_stop is not None and abs(old_stop - target) <= target * 1e-9:
                conn.rollback()
                continue
            scenario[C.SCENARIO_KEY] = updated
            conn.execute(_WRITE_SQL[market], (json.dumps(scenario, ensure_ascii=False), target, row["id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        kind = "promoted" if promoted else ("stop_armed" if facts.get("armed") else "stop_set")
        attributes = {"contract": LIVE_VERSION, "stop_loss": target, "previous_stop_loss": old_stop,
                      "entry_first": first, "facts": facts,
                      **({"promoted_on": updated["promoted_on"], "regime": updated.get("regime")} if promoted else {})}
        actions.append({"action": kind, "ticker": row["ticker"], "key": key, **attributes})
        try:
            _emit(f"core_hold.{kind}", market=market, ticker=row["ticker"], row_id=row["id"],
                  scenario=json.dumps(scenario, ensure_ascii=False), attributes=attributes)
        except Exception as error:  # noqa: BLE001 - an event never undoes a written stop
            _LOG.warning("[CORE_HOLD][%s] %s event failed: %s", market, row["ticker"], type(error).__name__)
        if alert and (promoted or facts.get("armed")) and abs((old_stop or 0) - target) > target * 1e-9:
            alert(_alert_text(market, row["ticker"], kind, updated, target, old_stop))
    return actions


def _decided(scenario):
    block = _load(scenario).get(C.SCENARIO_KEY)
    return isinstance(block, dict) and block.get("version") == LIVE_VERSION


def _mark_skipped(conn, market, rows, result, now):
    """Write status CAP_SKIPPED on the ticker's rows (stop untouched) so the slot rule stays 'no replacement'."""
    marked = 0
    for row in rows:
        if row["ticker"] != result["ticker"]:
            continue
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            fresh = conn.execute(_ROW_SQL[market], (row["id"],)).fetchone()
            scenario = _load(fresh[0]) if fresh else None
            if scenario is None or _decided(scenario):
                conn.rollback()
                continue
            scenario[C.SCENARIO_KEY] = {"version": LIVE_VERSION, "status": "CAP_SKIPPED",
                                        "eligible_on": result["a"]["date"], "at": now.isoformat()}
            conn.execute(_WRITE_SQL[market], (json.dumps(scenario, ensure_ascii=False), fresh[1], row["id"]))
            conn.commit()
            marked += 1
        except Exception:
            conn.rollback()
            raise
    return marked


def _alert_text(market, ticker, kind, block, stop, old_stop):
    name = {"KR": "한국", "US": "미국"}.get(market, market)
    if kind == "promoted":
        regime = (block.get("regime") or {}).get("regime") or "미확인"
        return (f"[장기 보유] {name} {ticker} 승격({block['promoted_on']}, 국면 {regime}). 손절가 {old_stop} → {stop}"
                "(최초 매수가). 200일선 이탈 확인 전에는 다른 매도를 막습니다.")
    return (f"[장기 보유] {name} {ticker} 200일선 아래 마감({(block.get('last') or {}).get('date')}). 손절가 {old_stop} → "
            f"{stop}(그날 저가). 다음 거래일 이 아래로 내려가면 하드스탑이 매도합니다.")
