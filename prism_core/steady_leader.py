"""Steady leader pullback detector (trigger T-B, "Coca-Cola type"), preregistered 2026-10-22.

Pure functions: no I/O, no clock, no provider calls. The caller passes completed daily bars
(Open/High/Low/Close/Volume, ascending dates); the last row is the evaluated session ``d``.
Too little history or a malformed bar returns ``status='MISSING'`` with a reason code — never
a pass and never zeros.

Source: ``tasks/research-20261022/new-triggers/PREREG_ko.md`` section "트리거 T-B" and its
수정 기록 items 2-3; research implementation ``newtrig.py`` (``TB`` mask, ``score_b``). The
formulas below are the research formulas, including its rolling-window minimum periods, so
the same bars give the same signal (``tests/test_steady_leader.py`` checks this against a
port of the research expressions and against the research's KO 2026 signal dates).
The definition is fixed: do not tune it here; a change is a new registered version.

Gates (all must hold for PASS; MAs include day d unless noted):
  trend   Close_d > MA50_d                                                     BELOW_MA50
          MA50_d > MA200_d                                                     MA50_BELOW_MA200
          MA50_d > MA50_{d-10}                                                 MA50_NOT_RISING
          MA200_d > MA200_{d-20}                                               MA200_NOT_RISING
          Close_d >= 0.90 x highest close of the prior 252 sessions (d excl.)  FAR_FROM_HIGH
  pullback some j in d-5..d-1 with Low_j <= MA20_j x 1.01 or Low_j <= MA10_j  NO_PULLBACK
  signal  Close_d > Close_{d-1}                                                NOT_UP_DAY
          Close_d > MA10_d                                                     BELOW_MA10
          Volume_d > max volume of down days (Close < previous Close) in the
          prior 10 sessions; no down day -> no signal (no 15-session fallback) NO_DOWN_DAY /
                                                                               NO_VOLUME_SIGNATURE
          not (gap >= 2 x ATR20% or day change >= 3 x ATR20%)                  GAP_OR_SURGE
  ATR20% = mean True Range over d-20..d-1 / Close_{d-1} (same as prism_core.pocket_pivot).
  No relative-strength condition (the research found RS gates removed KO-type leaders).

Rank inputs (recorded, used by ``rank_candidates``): steadiness = share of closes above MA50
over d-119..d; 120-session return = Close_d / Close_{d-120} - 1. Recorded only: the natural
structural stop MA20_d x 0.99 and its distance, and the prior 252-session high.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

DEFINITION_VERSION = "steady_leader_tb_v1_20261022"
TRIGGER_TYPE = "Steady Leader Pullback"
TRIGGER_LABEL_KO = "꾸준 상승 주도주 눌림"

MIN_HISTORY = 200           # sessions before d (research: more than 200 closes, MA200 needs them)
MA200_MIN_PERIODS = 180     # research MA200 = rolling(200, min_periods=180)
HIGH_WINDOW = 252           # prior-session highest close window
HIGH_MIN_PERIODS = 200      # research HI252 = shift(1).rolling(252, min_periods=200)
NEAR_HIGH = 0.90
PULLBACK_SESSIONS = 5
PULLBACK_MA20_BUFFER = 1.01
DOWN_VOLUME_WINDOW = 10
STEADY_WINDOW = 120
STEADY_MIN_PERIODS = 100    # research STEADY = rolling(120, min_periods=100)
RETURN_SESSIONS = 120
STRUCTURAL_STOP_BUFFER = 0.99
RANK_TOP_N = 2


def _missing(code: str, **metrics) -> dict:
    return {"status": "MISSING", "reason_codes": [code], "metrics": metrics,
            "version": DEFINITION_VERSION}


def _date_text(value) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)


def _clean(value):
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _ma(close: np.ndarray, j: int, length: int, min_periods: int | None = None) -> float:
    """Mean of closes j-length+1..j (pandas rolling semantics with ``min_periods``)."""
    lo = max(0, j - length + 1)
    window = close[lo:j + 1]
    need = length if min_periods is None else min_periods
    return float(window.mean()) if len(window) >= need else float("nan")


def detect(bars: pd.DataFrame, *, signal_date=None) -> dict:
    """Evaluate T-B on the last bar (or on ``signal_date``).

    Returns ``{'status': 'PASS'|'FAIL'|'MISSING', 'reason_codes', 'metrics', 'version'}``.
    FAIL lists every failed gate; MISSING says why the answer is unknown.
    """
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return _missing("NO_HISTORY")
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(bars.columns):
        return _missing("BAD_COLUMNS")
    b = bars.sort_index()
    if signal_date is not None:
        target = pd.Timestamp(signal_date).normalize()
        index = pd.to_datetime(b.index)
        if getattr(index, "tz", None) is not None:
            index = index.tz_localize(None)
        index = index.normalize()
        b = b.loc[index <= target]
        if b.empty or index[len(b) - 1] != target:
            return _missing("NO_BAR")
    b = b[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    b = b.dropna(subset=["Close"])
    n = len(b) - 1
    if n < MIN_HISTORY:
        return _missing("SHORT_HISTORY", history=max(n, 0))
    O, H, L, C, V = (b[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close", "Volume"))
    if not (V[n] > 0) or not all(np.isfinite(x[n]) for x in (O, H, L)):
        return _missing("BAD_BAR")
    pc = np.r_[np.nan, C[:-1]]
    tr = np.fmax(np.fmax(H - L, np.abs(H - pc)), np.abs(L - pc))
    with np.errstate(all="ignore"):
        atrp = float(np.nanmean(tr[n - 20:n]) / C[n - 1])
    if not math.isfinite(atrp) or atrp <= 0 or not (C[n - 1] > 0):
        return _missing("BAD_HISTORY")

    ma10, ma20, ma50 = _ma(C, n, 10), _ma(C, n, 20), _ma(C, n, 50)
    ma50_prev = _ma(C, n - 10, 50)
    ma200 = _ma(C, n, 200, MA200_MIN_PERIODS)
    ma200_prev = _ma(C, n - 20, 200, MA200_MIN_PERIODS)
    prior = C[max(0, n - HIGH_WINDOW):n]
    hi252 = float(prior.max()) if len(prior) >= HIGH_MIN_PERIODS else float("nan")
    if not all(math.isfinite(x) for x in (ma10, ma20, ma50, ma50_prev, ma200, ma200_prev, hi252)):
        return _missing("SHORT_HISTORY", history=n)

    m: dict = {"signal_date": _date_text(b.index[n]), "close": C[n], "atr20pct": atrp,
               "ma10": ma10, "ma20": ma20, "ma50": ma50, "ma200": ma200,
               "ma50_slope10_pct": (ma50 / ma50_prev - 1) * 100,
               "ma200_slope20_pct": (ma200 / ma200_prev - 1) * 100,
               "high_252": hi252, "close_vs_high_252": C[n] / hi252 - 1}
    rc: list[str] = []

    # Trend
    if not C[n] > ma50:
        rc.append("BELOW_MA50")
    if not ma50 > ma200:
        rc.append("MA50_BELOW_MA200")
    if not ma50 > ma50_prev:
        rc.append("MA50_NOT_RISING")
    if not ma200 > ma200_prev:
        rc.append("MA200_NOT_RISING")
    if not C[n] >= NEAR_HIGH * hi252:
        rc.append("FAR_FROM_HIGH")

    # Pullback in d-5..d-1 (MAs as of each day)
    pull_day = None
    for j in range(n - PULLBACK_SESSIONS, n):
        if L[j] <= _ma(C, j, 20) * PULLBACK_MA20_BUFFER or L[j] <= _ma(C, j, 10):
            pull_day = j
    m["pullback_date"] = _date_text(b.index[pull_day]) if pull_day is not None else None
    if pull_day is None:
        rc.append("NO_PULLBACK")

    # Signal day
    if not C[n] > C[n - 1]:
        rc.append("NOT_UP_DAY")
    if not C[n] > ma10:
        rc.append("BELOW_MA10")
    down = [j for j in range(n - DOWN_VOLUME_WINDOW, n) if C[j] < C[j - 1] and np.isfinite(V[j])]
    if not down:
        rc.append("NO_DOWN_DAY")
        m["signature_strength"] = None
    else:
        maxdv = max(V[j] for j in down)
        m["signature_strength"] = V[n] / maxdv if maxdv > 0 else None
        if not V[n] > maxdv:
            rc.append("NO_VOLUME_SIGNATURE")
    gap, chg = O[n] / C[n - 1] - 1, C[n] / C[n - 1] - 1
    m["gap_atr"], m["chg_atr"] = gap / atrp, chg / atrp
    if gap >= 2 * atrp or chg >= 3 * atrp:
        rc.append("GAP_OR_SURGE")

    # Rank inputs
    above = [C[j] > _ma(C, j, 50) for j in range(n - STEADY_WINDOW + 1, n + 1) if j >= 49]
    m["steadiness"] = (sum(above) / len(above)) if len(above) >= STEADY_MIN_PERIODS else None
    m["ret_120"] = C[n] / C[n - RETURN_SESSIONS] - 1 if C[n - RETURN_SESSIONS] > 0 else None
    if m["steadiness"] is None or m["ret_120"] is None:
        return _missing("SHORT_HISTORY", history=n)

    # Recorded only: natural structural stop and the prior high as first resistance
    stop = ma20 * STRUCTURAL_STOP_BUFFER
    m["structural_stop"] = stop
    m["structural_stop_pct"] = (stop / C[n] - 1) * 100
    m["high_252_vs_close_pct"] = (hi252 / C[n] - 1) * 100

    m = {k: _clean(v) for k, v in m.items()}
    return {"status": "PASS" if not rc else "FAIL", "reason_codes": rc, "metrics": m,
            "version": DEFINITION_VERSION}


def rank_candidates(rows: Iterable[Mapping], *, top_n: int = RANK_TOP_N) -> list[dict]:
    """Daily ranking for one market (research ``score_b``): 0.5 x steadiness percentile +
    0.5 x 120-session return percentile, both within that day's candidates.

    Each row needs ``ticker``, ``steadiness`` and ``ret_120``. Returns new dicts sorted by
    score (ties keep input order) with ``steadiness_pct``, ``ret_120_pct``, ``rank_score``,
    ``rank`` and ``top`` added.
    """
    items = [dict(row) for row in rows]
    if not items:
        return []
    steady = pd.Series([float(r["steadiness"]) for r in items]).rank(pct=True).to_numpy()
    ret = pd.Series([float(r["ret_120"]) for r in items]).rank(pct=True).to_numpy()
    for item, s, r in zip(items, steady, ret):
        item["steadiness_pct"] = float(s)
        item["ret_120_pct"] = float(r)
        item["rank_score"] = 0.5 * float(s) + 0.5 * float(r)
    ordered = sorted(enumerate(items), key=lambda pair: (-pair[1]["rank_score"], pair[0]))
    result = []
    for rank, (_, item) in enumerate(ordered, start=1):
        item["rank"] = rank
        item["top"] = rank <= top_n
        result.append(item)
    return result
