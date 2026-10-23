"""Core long-term holding rule, record only (H-1023-corehold, user direction 2026-10-23).

"Within the 10 slots, keep one or two Coca-Cola-like long uptrends; a core holding is not sold
before it breaks the 200-day line." This module holds the deterministic rule on completed daily
bars (oldest first, {date, open, high, low, close}); the observer (tools/run_core_hold_observer.py)
applies it to real positions and records what the rule would have done. Nothing here sells,
blocks a sell or writes a holding: promotion to LIVE needs the user's explicit approval.

Definitions follow tasks/research-20261023/core-hold/PREREG_ko.md (path A, T40G30 x S200):
- promotion, once, on the first confirmed close that meets all of: session >= 40 (entry
  session = 1), close >= entry x 1.30, close > MA50 > MA200, MA200 above its value 20 sessions
  earlier, close <= 1.4 x MA50 (the runner rule's overheat cap);
- at most CAP core positions per market; same-day candidates ranked by the steady-leader score
  (0.5 x percentile of the share of the last 120 sessions above MA50 + 0.5 x percentile of the
  120-session return), ties by ticker; a candidate that finds the slots full is skipped for good;
- core exit from the session after promotion: 200-day break confirmed the next day (close_t < MA200_t
  and low_t+1 < low_t -> sell on t+1 at min(open_t+1, low_t)), or breakeven (low <= entry ->
  min(entry, open)); no time cap;
- counterfactual (b), trailing stop exempt: after a real sale the position follows the research
  baseline S0a (runner rule R5, else two closes under MA50 -> next open, -7% stop) until promotion.
A value that cannot be computed (short history, missing bar) is None: never a promotion, never a sell.
"""
from __future__ import annotations

VERSION = "core-hold-v1-record"
SCENARIO_KEY = "core_hold"       # LIVE-time scenario key (record-only keeps state in runtime/)
MIN_SESSIONS = 40
GAIN = 0.30
OVERHEAT = 1.4
SLOPE_LAG = 20
CAP = 2
RANK_WINDOW = 120
TRAIL_ACTIVATION = 0.05          # the trailing stop arms at peak >= entry x 1.05 (cores/oneil_fallback.py)
CF_HARD_STOP = 0.07              # counterfactual (b): the absolute -7% rule still applies


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def closes_of(bars):
    return [_num(b.get("close")) for b in bars]


def ma(closes, index, window):
    """Simple MA of the ``window`` closes ending at ``index`` (inclusive), or None."""
    if index < 0 or index + 1 < window:
        return None
    part = closes[index + 1 - window:index + 1]
    return None if None in part else sum(part) / window


def session_number(bars, index, entry_session):
    """Session count with the entry session = 1, or None when the history starts after the entry
    and is too short to prove the count is at least MIN_SESSIONS."""
    start = str(entry_session)
    counted = sum(1 for b in bars[:index + 1] if str(b["date"]) >= start)
    if bars and str(bars[0]["date"]) > start:          # history starts after the entry: a lower bound
        return counted if counted >= MIN_SESSIONS else None
    return counted


def promotion(bars, index, *, entry, entry_session):
    """The promotion conditions on bar ``index``: each True/False/None, plus ``eligible``."""
    closes = closes_of(bars)
    close, entry = closes[index], _num(entry)
    m50, m200 = ma(closes, index, 50), ma(closes, index, 200)
    lag = ma(closes, index - SLOPE_LAG, 200)
    sessions = session_number(bars, index, entry_session)
    gain = None if close is None or not entry else close / entry - 1

    def known(*values):
        return None not in values

    conditions = {
        "sessions": None if sessions is None else sessions >= MIN_SESSIONS,
        "gain": None if gain is None else gain >= GAIN,
        "trend": (close > m50 > m200) if known(close, m50, m200) else None,
        "ma200_rising": (m200 > lag) if known(m200, lag) else None,
        "not_overheated": (close <= OVERHEAT * m50) if known(close, m50) else None,
    }
    values = list(conditions.values())
    eligible = False if False in values else (None if None in values else True)
    return {"date": bars[index]["date"], "sessions": sessions, "gain": gain, "close": close, "ma50": m50,
            "ma200": m200, "ma200_lag": lag, "conditions": conditions, "eligible": eligible}


def rank_inputs(bars, index):
    """(share of the last 120 sessions closing above MA50, 120-session return), None when unknown."""
    closes = closes_of(bars)
    if index < RANK_WINDOW:
        return None, None
    flags = []
    for i in range(index - RANK_WINDOW + 1, index + 1):
        m50 = ma(closes, i, 50)
        flags.append(None if m50 is None or closes[i] is None else closes[i] > m50)
    share = None if None in flags else sum(flags) / RANK_WINDOW
    base = closes[index - RANK_WINDOW]
    ret = None if not base or closes[index] is None else closes[index] / base - 1
    return share, ret


def _percentiles(values):
    """Average-rank percentile in (0, 1]; None (unknown) ranks lowest."""
    known = sorted(v for v in values if v is not None)
    out = []
    for value in values:
        if value is None:
            out.append(0.0)
            continue
        below = sum(1 for v in known if v < value)
        equal = sum(1 for v in known if v == value)
        out.append((below + (equal + 1) / 2) / len(known))
    return out


def rank(candidates):
    """Order same-day candidates [{ticker, share, ret}] by the steady-leader score, ties by ticker."""
    shares = _percentiles([c.get("share") for c in candidates])
    rets = _percentiles([c.get("ret") for c in candidates])
    scored = [dict(c, score=0.5 * s + 0.5 * r) for c, s, r in zip(candidates, shares, rets)]
    return sorted(scored, key=lambda c: (-c["score"], str(c.get("ticker"))))


def core_exit(bars, *, start_index, entry):
    """First core exit after the promotion bar ``start_index``: {date, price, code, index} or None.

    Same-day breakeven and 200-day triggers: the higher stop fills first (min(open, that stop)).
    """
    closes = closes_of(bars)
    entry = _num(entry)
    for t in range(start_index + 1, len(bars)):
        bar = bars[t]
        low, open_ = _num(bar.get("low")), _num(bar.get("open"))
        stops = []
        if entry and low is not None and low <= entry:
            stops.append((entry, "BREAKEVEN"))
        prev = t - 1
        if prev > start_index:                       # the close below the line is after the promotion
            m200 = ma(closes, prev, 200)
            prev_low = _num(bars[prev].get("low"))
            if (m200 is not None and closes[prev] is not None and closes[prev] < m200
                    and prev_low is not None and low is not None and low < prev_low):
                stops.append((prev_low, "MA200_BREAK"))
        if stops:
            level, code = max(stops)
            price = level if open_ is None else min(open_, level)
            return {"date": bar["date"], "price": price, "code": code, "index": t}
    return None


def _runner_detected(bars, upto, *, first, entry_session):
    """The runner rule R5 (prism_core/runner_hold.py): on sessions 4..15 a close >= first x 1.2 that is
    not overheated (<= 1.4 x MA50). Returns True/False for the bars up to ``upto`` (inclusive)."""
    closes = closes_of(bars)
    for t in range(upto + 1):
        sessions = session_number(bars, t, entry_session)
        if sessions is None or not 4 <= sessions <= 15:
            continue
        m50 = ma(closes, t, 50)
        if closes[t] is not None and m50 is not None and closes[t] >= first * 1.2 and closes[t] <= OVERHEAT * m50:
            return True
    return False


def counterfactual_path(bars, *, start_index, entry, entry_session, first_entry=None, promote=True):
    """Variant (b) from the real sale bar: the first of promotion (index) or an engine exit.

    The engine is the research baseline S0a without the trailing stop: the runner rule R5 (stop at the
    first entry, close under MA50 or the entry -> next open, after 40 sessions close under MA20 -> next
    open), otherwise two closes under MA50 -> next open and the -7% hard stop on the first entry.
    Returns ("PROMOTED", promotion dict) | ("EXIT", {date, price, code, index}) | ("OPEN", None).
    Exits are checked before promotion on the same bar (promotion needs a day without a sell signal).
    ``promote=False`` follows the engine to its exit (a candidate that found the slots full).
    """
    closes = closes_of(bars)
    first = _num(first_entry) or _num(entry)
    if not first:
        return "OPEN", None
    runner = _runner_detected(bars, start_index - 1, first=first, entry_session=entry_session)
    pending = None
    for t in range(start_index, len(bars)):
        bar = bars[t]
        open_, low, close = _num(bar.get("open")), _num(bar.get("low")), closes[t]
        if pending is not None:                      # a close-based exit sells at this open
            return "EXIT", {"date": bar["date"], "price": open_, "code": pending, "index": t}
        stop = first if runner else first * (1 - CF_HARD_STOP)
        if low is not None and low <= stop:
            return "EXIT", {"date": bar["date"], "price": stop if open_ is None else min(open_, stop),
                            "code": "RUNNER_BREAKEVEN" if runner else "HARD_STOP", "index": t}
        runner = runner or _runner_detected(bars, t, first=first, entry_session=entry_session)
        m50, m20 = ma(closes, t, 50), ma(closes, t, 20)
        sessions = session_number(bars, t, entry_session)
        if runner and close is not None:
            if m50 is not None and close < m50:
                pending = "RUNNER_MA50"
            elif sessions is not None and sessions > 40 and m20 is not None and close < m20:
                pending = "RUNNER_MA20"
        elif close is not None and closes[t - 1] is not None:
            m50_prev = ma(closes, t - 1, 50)
            if m50 is not None and m50_prev is not None and close < m50 and closes[t - 1] < m50_prev:
                pending = "MA50_TWO_CLOSES"
        if pending is not None or not promote:
            continue
        view = promotion(bars, t, entry=entry, entry_session=entry_session)
        if view["eligible"]:
            return "PROMOTED", dict(view, index=t)
    return "OPEN", None


def trailing_armed(bars, *, entry, entry_session, until):
    """Did the peak after the buy reach entry x 1.05 by ``until`` (inclusive)? The entry session's high
    may predate the buy, so that session counts only with its close."""
    entry = _num(entry)
    peaks = [_num(b.get("close")) if str(b["date"]) == str(entry_session) else _num(b.get("high"))
             for b in bars if str(entry_session) <= str(b["date"]) <= str(until)]
    if not entry or not peaks or None in peaks:
        return None
    return max(peaks) >= entry * (1 + TRAIL_ACTIVATION)
