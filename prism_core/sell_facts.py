"""System-computed sell facts for the AI sell prompt (KR/US, 2026-10-19 prompt audit).

The AI used to recompute these from tool OHLCV on every review: the trailing line and whether a
stop raise qualifies (the result runs as an intraday stop), the tier-3 trend-weakness conditions,
the return against the micro-split average entry and the trading sessions held. The values here
come from the same rules and constants the exit code runs (cores/oneil_fallback.py) on completed
daily bars only. A value that cannot be computed is shown as unknown, never guessed.
"""

from __future__ import annotations

import os

from cores.oneil_fallback import (BULL_REGIMES, STOP_WICK_BUFFER, TRAIL_ACTIVATION_PCT, TRAIL_DROP_BULL,
                                  TRAIL_DROP_UNKNOWN, TRAIL_DROP_WEAK, _normalize_regime)
from prism_core import runner_hold as R

TREND_DOWN_CLOSES = 3      # tier 3: three consecutive lower daily closes
VOLUME_WINDOW = 20         # tier 3: volume against the prior 20-session average
MA_WINDOW = 20             # tier 3: close below the 20-day MA


def enabled():
    """SELL_FACTS_ENABLED (default true); off = the sell prompt carries no facts block (rollback)."""
    return os.getenv("SELL_FACTS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def trailing_state(*, buy_price, highest_price, stop_loss, current_price, regime, threshold_pct, last_close):
    """Trailing line and stop-raise eligibility exactly as the exit code computes them."""
    buy, peak = R._num(buy_price), R._num(highest_price)
    if buy is None or peak is None:
        return None
    if not regime:
        drop = TRAIL_DROP_UNKNOWN
    elif _normalize_regime(regime) in BULL_REGIMES:
        drop = TRAIL_DROP_BULL
    else:
        drop = TRAIL_DROP_WEAK
    line = peak * drop
    stop, price, close = R._num(stop_loss), R._num(current_price), R._num(last_close)
    gap = (line - stop) / stop * 100 if stop else None
    activated = peak >= buy * (1 + TRAIL_ACTIVATION_PCT / 100)
    raise_ok = bool(activated and regime and gap is not None and gap >= float(threshold_pct)
                    and price is not None and line < price)
    return {"activated": activated, "drop": drop, "regime_live": bool(regime), "peak": peak, "line": line,
            "trigger": line * (1 - STOP_WICK_BUFFER), "close": close,
            "close_below": None if close is None else close <= line * (1 - STOP_WICK_BUFFER),
            "gap_pct": gap, "threshold_pct": float(threshold_pct), "raise_ok": raise_ok}


def trend_weakness(bars):
    """Tier-3 conditions on completed bars (oldest first): each is True/False, or None when unknown."""
    closes = [R._num(b.get("close")) for b in bars or []]
    if not closes or closes[-1] is None:
        return None
    last = bars[-1]
    tail = closes[-(TREND_DOWN_CLOSES + 1):]
    down = (None if len(tail) < TREND_DOWN_CLOSES + 1 or None in tail
            else all(tail[i + 1] < tail[i] for i in range(TREND_DOWN_CLOSES)))
    window = closes[-MA_WINDOW:]
    ma20 = sum(window) / MA_WINDOW if len(window) == MA_WINDOW and None not in window else None
    volumes = [R._num(b.get("volume")) for b in bars[-(VOLUME_WINDOW + 1):-1]]
    today_volume = R._num(last.get("volume"))
    avg_volume = (sum(volumes) / VOLUME_WINDOW
                  if len(volumes) == VOLUME_WINDOW and None not in volumes else None)
    ratio = today_volume / avg_volume if today_volume is not None and avg_volume else None
    below = None if ma20 is None else closes[-1] < ma20
    volume_ok = None if ratio is None else ratio >= 1.0
    parts = (down, volume_ok, below)
    met = None if None in parts else all(parts)
    return {"date": last.get("date"), "close": closes[-1], "three_down": down, "volume_ratio": ratio,
            "volume_above": volume_ok, "ma20": ma20, "below_ma20": below, "met": met}


def tier3_counts(trend, held):
    """Tier 3 is a sell reason only when all three lower closes are completed bars after the entry session.

    Weakness that already existed before the buy was known at entry (user decision 2026-10-22, FRO re-entry
    sold 57 minutes after the buy on a pre-entry Tier-3 bar). True/False, or None when unknown.
    """
    if not trend or trend["met"] is None:
        return None
    if not trend["met"]:
        return False
    return None if held is None else held >= TREND_DOWN_CLOSES


def sessions_held(bars, market, buy_date):
    """Completed sessions after the entry session, or None."""
    try:
        start = R.entry_session(market, buy_date).isoformat()
    except Exception:  # noqa: BLE001 — a malformed buy_date stays unknown
        return None
    return sum(1 for b in bars or [] if str(b.get("date")) > start)


def average_entry(scenario):
    """(average entry, number of buys) for a micro-split holding with more than one leg, else None."""
    from prism_core.micro_split_live import record as micro_record, weighted_entry
    block = micro_record(R.load_scenario(scenario))
    legs = (block or {}).get("legs") or []
    if len(legs) < 2:
        return None
    try:
        return float(weighted_entry(legs)), len(legs)
    except Exception:  # noqa: BLE001
        return None


def _money(value, market, ko):
    if value is None:
        return "확인 불가" if ko else "unknown"
    if str(market).upper() == "US":
        return f"${float(value):,.2f}"
    return f"{float(value):,.0f}원" if ko else f"{float(value):,.0f} KRW"


def _yes(value, ko, yes=None, no=None):
    if value is None:
        return "확인 불가" if ko else "unknown"
    if value:
        return yes or ("예" if ko else "yes")
    return no or ("아니오" if ko else "no")


def prompt_block(*, market, language, stock_data, highest_price, threshold_pct, regime, bars):
    """The facts block for the sell user message. ``bars`` are completed daily bars (oldest first)."""
    ko = language == "ko"
    m = lambda v: _money(v, market, ko)  # noqa: E731
    trend = trend_weakness(bars)
    last_close = trend["close"] if trend else None
    held = sessions_held(bars, market, stock_data.get("buy_date")) if bars else None
    block = R.record(stock_data.get("scenario"))
    runner = bool(block and block.get("status") == R.RUNNER and R.enabled(market))
    lines = []
    if trend:
        lines.append((f"- 기준 일봉: {trend['date']} 확정 종가 {m(trend['close'])}" if ko else
                      f"- Reference bar: {trend['date']} confirmed close {m(trend['close'])}")
                     + ((f", 매수 후 {held}거래일째" if ko else f", {held} sessions since the entry")
                        if held is not None else ""))
    else:
        lines.append("- 확정 일봉: 확인 불가(조회 실패)" if ko else "- Completed daily bars: unknown (fetch failed)")
    if not runner:
        t = trailing_state(buy_price=stock_data.get("buy_price"), highest_price=highest_price,
                           stop_loss=stock_data.get("stop_loss"), current_price=stock_data.get("current_price"),
                           regime=regime, threshold_pct=threshold_pct, last_close=last_close)
        if t is None:
            lines.append("- 트레일링: 확인 불가" if ko else "- Trailing stop: unknown")
        elif not t["activated"]:
            lines.append(f"- 트레일링: 아직 꺼짐(고점 {m(t['peak'])}이 매수가 +5% 미만). 초기 손절가를 유지하십시오." if ko else
                         f"- Trailing stop: not active yet (peak {m(t['peak'])} is under entry +5%). Keep the initial stop.")
        else:
            band = f"× {t['drop']:.2f}" + ("" if t["regime_live"] else (" (국면 미확인)" if ko else " (regime unavailable)"))
            lines.append(
                (f"- 트레일링 라인: 고점 {m(t['peak'])} {band} = {m(t['line'])} (자동 청산은 {m(t['trigger'])} 이하). "
                 f"최근 확정 종가는 라인 {_yes(t['close_below'], ko, '아래 → 매도', '위')}." if ko else
                 f"- Trailing line: peak {m(t['peak'])} {band} = {m(t['line'])} (automatic exit at or below "
                 f"{m(t['trigger'])}). Latest confirmed close is {_yes(t['close_below'], ko, 'below it → sell', 'above it')}."))
            gap = _yes(None, ko) if t["gap_pct"] is None else f"{t['gap_pct']:+.1f}%"
            lines.append(
                (f"- 손절선 상향 자격: {_yes(t['raise_ok'], ko, '있음', '없음')} (라인이 현재 손절가 {m(R._num(stock_data.get('stop_loss')))} 대비 "
                 f"{gap}, 임계값 {t['threshold_pct']:.1f}%, 라인이 현재가보다 낮아야 함"
                 + ("" if t["regime_live"] else ", 국면 미확인 날은 올리지 않음") + ")" if ko else
                 f"- Stop raise qualifies: {_yes(t['raise_ok'], ko)} (line vs current stop "
                 f"{m(R._num(stock_data.get('stop_loss')))}: {gap}, threshold {t['threshold_pct']:.1f}%, the line must be "
                 "below the current price" + ("" if t["regime_live"] else ", no raise while the regime is unavailable")
                 + ")"))
    if trend:
        counts = tier3_counts(trend, held)
        after = ""
        if trend["met"]:
            after_entry = None if held is None else held >= TREND_DOWN_CLOSES
            after = (f". 하락 3거래일이 모두 매수 후 확정 일봉: {_yes(after_entry, ko)} → 3단계 매도 근거 {_yes(counts, ko)}"
                     + ("(매수 전부터 있던 약세는 세지 않음)" if counts is False else "") if ko else
                     f". All three lower closes after the entry session: {_yes(after_entry, ko)} → Tier-3 sell reason "
                     f"{_yes(counts, ko)}" + (" (weakness from before the buy does not count)" if counts is False else ""))
        ratio = _yes(None, ko) if trend["volume_ratio"] is None else f"{trend['volume_ratio']:.2f}"
        lines.append(
            (f"- 3단계 추세 약화 조건: 3거래일 연속 종가 하락 {_yes(trend['three_down'], ko)}, 기준일 거래량/직전 20일 평균 "
             f"{ratio}배({_yes(trend['volume_above'], ko, '평균 이상', '평균 미만')}), 종가가 20일선 {m(trend['ma20'])} "
             f"{_yes(trend['below_ma20'], ko, '아래', '위')} → 3개 모두 충족 {_yes(trend['met'], ko)}" + after if ko else
             f"- Tier-3 trend-weakness conditions: 3 consecutive lower closes {_yes(trend['three_down'], ko)}, "
             f"reference-bar volume / prior 20-day average {ratio}x ({_yes(trend['volume_above'], ko, 'at or above', 'below')}), "
             f"close {_yes(trend['below_ma20'], ko, 'below', 'above')} the 20-day MA {m(trend['ma20'])} → all three met "
             f"{_yes(trend['met'], ko)}" + after))
    avg = average_entry(stock_data.get("scenario"))
    price = R._num(stock_data.get("current_price"))
    if avg and price:
        lines.append(
            (f"- 초분할 평균 매수가 {m(avg[0])}({avg[1]}회 매수), 평균 기준 수익률 {(price / avg[0] - 1) * 100:+.2f}% "
             "(위 '수익률'·손절가·-7%는 최초 매수가 기준)" if ko else
             f"- Micro-split average entry {m(avg[0])} ({avg[1]} buys), return vs the average "
             f"{(price / avg[0] - 1) * 100:+.2f}% (the 'Return' above, the stop and -7% use the first buy)"))
    head = ("\n### 시스템 계산 사실 (확정 일봉 기준, 자동 청산과 같은 계산)\n" if ko else
            "\n### System-computed facts (completed daily bars, same math as the automatic exit)\n")
    tail = ("이 값을 다시 계산하지 말고 그대로 쓰십시오. '확인 불가'인 값만 도구로 확인하고, 추정으로 채우지 마십시오.\n" if ko else
            "Use these values as given; do not recompute them. Check only 'unknown' values with tools, and never "
            "fill them with estimates.\n")
    return head + "\n".join(lines) + "\n" + tail


def prompt_block_safe(*, market, language, stock_data, highest_price, threshold_pct, regime, now=None, logger=None):
    """Fetch completed bars and build the block; any failure yields '' (the prompt then stays as before)."""
    if not enabled():
        return ""
    try:
        from prism_core import runner_hold_live as L
        when = now or L._now()
        try:
            bars = R.completed_bars(L.fetch_daily_bars(market, stock_data.get("ticker"), when), market=market, now=when)
        except Exception as err:  # noqa: BLE001 — bars unknown, the block says so
            if logger:
                logger.warning(f"[sell_facts] {stock_data.get('ticker')} bars unavailable: {err}")
            bars = []
        return prompt_block(market=market, language=language, stock_data=stock_data, highest_price=highest_price,
                            threshold_pct=threshold_pct, regime=regime, bars=bars)
    except Exception as err:  # noqa: BLE001
        if logger:
            logger.warning(f"[sell_facts] {stock_data.get('ticker')} facts block skipped: {err}")
        return ""
